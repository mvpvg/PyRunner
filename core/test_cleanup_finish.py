"""
Finish-up cleanups for the 2026-07 code review.

- 4a.5 The two byte-identical duration formatters (``Run.duration_display`` and
       ``TaskService._format_duration``) now share ``core.formatting.format_duration``.
- 5.6  Per-IP rate-limit keys derive the client IP through ``client_ip``, which
       honors ``RATELIMIT_TRUSTED_PROXY_DEPTH`` so a reverse-proxy deploy doesn't
       collapse every caller onto the proxy IP — and reads X-Forwarded-For from the
       RIGHT (spoof-resistant), never the client-controlled left.
"""

from django.test import RequestFactory, SimpleTestCase, override_settings

from core.formatting import format_duration
from core.ratelimit import client_ip
from core.services.task_service import TaskService


class FormatDurationTests(SimpleTestCase):
    def test_values(self):
        self.assertEqual(format_duration(None), "-")
        self.assertEqual(format_duration(5), "5.0s")
        self.assertEqual(format_duration(90), "1m 30s")
        self.assertEqual(format_duration(3700), "1h 1m")

    def test_task_service_delegates(self):
        self.assertEqual(TaskService._format_duration(90), format_duration(90))
        self.assertEqual(TaskService._format_duration(None), "-")


class ClientIpProxyDepthTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def _req(self, remote, xff=None):
        extra = {"REMOTE_ADDR": remote}
        if xff is not None:
            extra["HTTP_X_FORWARDED_FOR"] = xff
        return self.rf.get("/", **extra)

    @override_settings(RATELIMIT_TRUSTED_PROXY_DEPTH=0)
    def test_default_uses_remote_addr_ignoring_xff(self):
        req = self._req("5.5.5.5", xff="9.9.9.9, 1.1.1.1")
        self.assertEqual(client_ip(req), "5.5.5.5")

    @override_settings(RATELIMIT_TRUSTED_PROXY_DEPTH=1)
    def test_depth_one_takes_rightmost_ignoring_spoof(self):
        # Client spoofs 9.9.9.9; the single trusted proxy appended the real 1.1.1.1.
        req = self._req("proxy", xff="9.9.9.9, 1.1.1.1")
        self.assertEqual(client_ip(req), "1.1.1.1")

    @override_settings(RATELIMIT_TRUSTED_PROXY_DEPTH=2)
    def test_depth_two(self):
        req = self._req("proxyB", xff="client, proxyA")
        self.assertEqual(client_ip(req), "client")

    @override_settings(RATELIMIT_TRUSTED_PROXY_DEPTH=1)
    def test_missing_xff_falls_back_to_remote_addr(self):
        self.assertEqual(client_ip(self._req("5.5.5.5")), "5.5.5.5")
