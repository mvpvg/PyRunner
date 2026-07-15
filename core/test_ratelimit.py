"""
Rate-limit helper tests.

Regression for review 5.1: every rate-limit site used
``cache.set(key, count + 1, WINDOW)``, which re-arms the TTL on every hit —
under steady sub-limit traffic the window never expired, so a legitimate
caller (e.g. an API poller at half the limit) accumulated across windows and
starved itself with 429s. The shared helper (core/ratelimit.py) arms the TTL
once per window (add + incr) and increments atomically.
"""

import uuid
from unittest import mock

from django.test import TestCase

from core.models import Environment, GlobalSettings, User
from core.ratelimit import rate_limit_exceeded


def _key():
    return f"test_rl_{uuid.uuid4().hex}"


class RateLimitHelperTests(TestCase):
    def test_allows_exactly_limit_hits_then_rejects(self):
        key = _key()
        results = [rate_limit_exceeded(key, 3, 60) for _ in range(5)]
        self.assertEqual(results, [False, False, False, True, True])

    def test_keys_are_independent(self):
        a, b = _key(), _key()
        for _ in range(3):
            rate_limit_exceeded(a, 3, 60)
        self.assertTrue(rate_limit_exceeded(a, 3, 60))
        self.assertFalse(rate_limit_exceeded(b, 3, 60))

    def test_window_is_fixed_not_sliding(self):
        """Hits inside the window must NOT extend it — the 5.1 regression.

        Under the old set()-per-hit pattern, mid-window hits re-armed the TTL,
        so the (full) counter was still alive after the original window ended
        and the final hit here would have seen a 429. Deterministic via a
        mocked clock (the window bucket is derived from time.time()).
        """
        key = _key()
        with mock.patch("core.ratelimit.time") as clock:
            clock.time.return_value = 1200.0  # bucket 20 of a 60s window
            self.assertFalse(rate_limit_exceeded(key, 2, 60))
            clock.time.return_value = 1230.0  # same bucket: budget used up
            self.assertFalse(rate_limit_exceeded(key, 2, 60))
            self.assertTrue(rate_limit_exceeded(key, 2, 60))  # over budget
            clock.time.return_value = 1261.0  # next window: fresh budget
            self.assertFalse(rate_limit_exceeded(key, 2, 60))

    def test_cull_race_still_counts_the_hit(self):
        # incr() raising ValueError (key culled between add and incr) must
        # neither crash nor drop the brake: the hit lands on a fresh counter.
        with mock.patch("core.ratelimit.cache") as c:
            c.add.return_value = False
            c.incr.side_effect = ValueError("no such key")
            self.assertFalse(rate_limit_exceeded(_key(), 3, 60))
            c.set.assert_called_once()  # counted as 1 on a fresh counter


class WebhookRateLimitIntegrationTests(TestCase):
    """The public webhook path still 429s over budget (behavior preserved
    through the helper swap). The limit applies before token lookup, so an
    unknown token exercises it without any fixture."""

    def setUp(self):
        # Past the setup wizard (setup flag + default env + a superuser), or
        # its middleware 302s the webhook path to /setup/.
        gs = GlobalSettings.get_settings()
        gs.setup_completed = True
        gs.save()
        Environment.objects.get_or_create(
            name="default", defaults={"is_default": True, "python_version": "3.12"}
        )
        User.objects.create(email="admin@example.com", is_superuser=True)

    @mock.patch("core.views.webhooks.WEBHOOK_RATE_LIMIT", 2)
    def test_third_request_is_rate_limited(self):
        url = f"/webhook/{uuid.uuid4().hex}/"
        first = self.client.post(url)
        second = self.client.post(url)
        third = self.client.post(url)

        self.assertEqual(first.status_code, 404)  # unknown token, budget spent
        self.assertEqual(second.status_code, 404)
        self.assertEqual(third.status_code, 429)
