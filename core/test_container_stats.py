"""
Container-aware system resource stats.

psutil reads /proc/stat and /proc/meminfo, which Docker does NOT virtualize —
inside a container they describe the whole host (VPS). The dashboard therefore
reads the container's own numbers from the cgroup filesystem (the same source
`docker stats` uses) and demotes the psutil numbers to a secondary "host"
block. These tests fake the cgroup tree on disk (v2 and v1 layouts) and verify:

- memory matches `docker stats` semantics (usage minus reclaimable page cache),
  against the container limit when one is set, else against host total RAM;
- v1's PAGE_COUNTER_MAX "no limit" sentinel is treated as uncapped;
- CPU percent is normalized to the container's quota when set, else host cores;
- without cgroup files (bare metal, Windows dev) the payload falls back to the
  pre-existing host-only shape so non-Docker installs render unchanged.
"""

import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.models import Environment, GlobalSettings, User, Workspace, WorkspaceMembership
from core.services.system_info_service import SystemInfoService

GiB = 1024 * 1024 * 1024
MiB = 1024 * 1024

FAKE_HOST_MEMORY = {
    "total": 8 * GiB,
    "used": 5 * GiB,
    "available": 3 * GiB,
    "percent": 62.5,
    "total_display": "8.0 GB",
    "used_display": "5.0 GB",
}

FAKE_DISK = {
    "total": 100 * GiB,
    "used": 40 * GiB,
    "free": 60 * GiB,
    "percent": 40.0,
    "total_display": "100.0 GB",
    "used_display": "40.0 GB",
}


def _write_tree(root: Path, files: dict):
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


class ContainerStatsTestCase(SimpleTestCase):
    """Base: fake cgroup root + hermetic psutil (no real 100ms sampling)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cgroup_root = Path(self._tmp.name)
        self._patch("core.services.system_info_service.CGROUP_ROOT", self.cgroup_root)

    def _patch(self, target, replacement):
        patcher = mock.patch(target, replacement)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _get_resources(self, host_cpu=33.3, cpu_usage_after=None):
        """
        Call get_system_resources with psutil fully mocked.

        The container CPU counter is sampled before and after the (normally
        blocking) host psutil read; `cpu_usage_after` rewrites the counter
        file inside that window, and monotonic time is pinned to a 0.1s gap.
        """

        def fake_host_cpu():
            if cpu_usage_after is not None:
                _write_tree(self.cgroup_root, cpu_usage_after)
            return host_cpu

        with mock.patch.object(
            SystemInfoService, "get_cpu_usage", side_effect=fake_host_cpu
        ), mock.patch.object(
            SystemInfoService, "get_memory_info", return_value=dict(FAKE_HOST_MEMORY)
        ), mock.patch.object(
            SystemInfoService, "get_disk_info", return_value=dict(FAKE_DISK)
        ), mock.patch("core.services.system_info_service.time") as fake_time:
            fake_time.monotonic.side_effect = [10.0, 10.1]
            return SystemInfoService.get_system_resources()


class CgroupV2Tests(ContainerStatsTestCase):
    def _write_v2(self, memory_max="2147483648", cpu_max="400000 100000"):
        _write_tree(
            self.cgroup_root,
            {
                # 800 MiB current, 100 MiB reclaimable page cache → 700 MiB used
                "memory.current": str(800 * MiB),
                "memory.stat": f"anon {600 * MiB}\ninactive_file {100 * MiB}\n",
                "memory.max": memory_max,
                "cpu.stat": "usage_usec 50000\nuser_usec 30000\n",
                "cpu.max": cpu_max,
            },
        )

    def test_memory_against_container_limit(self):
        """With a 2 GiB limit: used excludes page cache, percent is of the limit."""
        self._write_v2()
        result = self._get_resources()

        self.assertTrue(result["in_container"])
        memory = result["memory"]
        self.assertEqual(memory["used"], 700 * MiB)
        self.assertEqual(memory["total"], 2 * GiB)
        self.assertEqual(memory["available"], 2 * GiB - 700 * MiB)
        self.assertEqual(memory["percent"], 34.2)
        self.assertTrue(memory["is_limit"])
        self.assertEqual(memory["used_display"], "700.0 MB")
        self.assertEqual(memory["total_display"], "2.0 GB")

    def test_memory_uncapped_uses_host_total(self):
        """memory.max = "max": percent is against host RAM, is_limit False."""
        self._write_v2(memory_max="max")
        result = self._get_resources()

        memory = result["memory"]
        self.assertEqual(memory["used"], 700 * MiB)
        self.assertEqual(memory["total"], FAKE_HOST_MEMORY["total"])
        self.assertFalse(memory["is_limit"])
        self.assertEqual(memory["percent"], 8.5)  # 700 MiB of 8 GiB

    def test_cpu_percent_normalized_to_quota(self):
        """200ms of CPU over a 100ms window with a 4-CPU quota → 50%."""
        self._write_v2()  # cpu.max "400000 100000" = 4 CPUs
        result = self._get_resources(
            cpu_usage_after={"cpu.stat": "usage_usec 250000\nuser_usec 100000\n"}
        )
        self.assertEqual(result["cpu"]["percent"], 50.0)

    def test_cpu_percent_no_quota_uses_host_cores(self):
        """cpu.max = "max ...": normalize against the host core count instead."""
        self._write_v2(cpu_max="max 100000")
        with mock.patch(
            "core.services.system_info_service.psutil.cpu_count", return_value=2
        ):
            result = self._get_resources(
                cpu_usage_after={"cpu.stat": "usage_usec 150000\n"}
            )
        # 100ms of CPU over a 100ms window on 2 cores → 50%
        self.assertEqual(result["cpu"]["percent"], 50.0)

    def test_host_block_carries_psutil_numbers(self):
        """The demoted VPS numbers ride along for the secondary display line."""
        self._write_v2()
        result = self._get_resources(host_cpu=42.7)

        self.assertEqual(result["host"]["cpu"]["percent"], 42.7)
        self.assertEqual(result["host"]["memory"], FAKE_HOST_MEMORY)
        # Disk is never split: same filesystem inside and outside the container.
        self.assertEqual(result["disk"], FAKE_DISK)


class CgroupV1Tests(ContainerStatsTestCase):
    def _write_v1(self, limit=str(2 * GiB), quota="-1"):
        _write_tree(
            self.cgroup_root,
            {
                "memory/memory.usage_in_bytes": str(800 * MiB),
                "memory/memory.stat": f"total_inactive_file {100 * MiB}\n",
                "memory/memory.limit_in_bytes": limit,
                "cpuacct/cpuacct.usage": "50000000",  # nanoseconds = 50000 usec
                "cpu/cpu.cfs_quota_us": quota,
                "cpu/cpu.cfs_period_us": "100000",
            },
        )

    def test_v1_layout_parsed(self):
        """Legacy paths: usage_in_bytes/total_inactive_file/cpuacct.usage."""
        self._write_v1()
        with mock.patch(
            "core.services.system_info_service.psutil.cpu_count", return_value=4
        ):
            result = self._get_resources(
                cpu_usage_after={"cpuacct/cpuacct.usage": "250000000"}
            )

        self.assertTrue(result["in_container"])
        self.assertEqual(result["memory"]["used"], 700 * MiB)
        self.assertEqual(result["memory"]["total"], 2 * GiB)
        self.assertTrue(result["memory"]["is_limit"])
        # 200ms CPU over 100ms window, no quota (-1) → 4 host cores → 50%
        self.assertEqual(result["cpu"]["percent"], 50.0)

    def test_v1_unlimited_sentinel_means_uncapped(self):
        """PAGE_COUNTER_MAX limit (no mem_limit configured) → host total."""
        self._write_v1(limit="9223372036854771712")
        result = self._get_resources()

        self.assertEqual(result["memory"]["total"], FAKE_HOST_MEMORY["total"])
        self.assertFalse(result["memory"]["is_limit"])

    def test_v1_quota_beats_host_cores(self):
        """cfs_quota_us 200000 / period 100000 = 2 CPUs → 200ms/100ms = 100%."""
        self._write_v1(quota="200000")
        result = self._get_resources(
            cpu_usage_after={"cpuacct/cpuacct.usage": "250000000"}
        )
        self.assertEqual(result["cpu"]["percent"], 100.0)


class FallbackTests(ContainerStatsTestCase):
    def test_no_cgroup_files_falls_back_to_host_shape(self):
        """Bare metal / Windows dev: pre-existing host-only payload, host=None."""
        result = self._get_resources(host_cpu=18.2)

        self.assertFalse(result["in_container"])
        self.assertIsNone(result["host"])
        self.assertEqual(result["cpu"]["percent"], 18.2)
        self.assertEqual(result["memory"], FAKE_HOST_MEMORY)
        self.assertEqual(result["disk"], FAKE_DISK)

    def test_corrupt_cgroup_files_fall_back(self):
        """Unparseable counters must degrade to host stats, not error."""
        _write_tree(
            self.cgroup_root,
            {
                "memory.current": "not-a-number",
                "memory.stat": "inactive_file 0\n",
                "memory.max": "max",
                "cpu.stat": "usage_usec 50000\n",
                "cpu.max": "max 100000",
            },
        )
        result = self._get_resources()
        self.assertFalse(result["in_container"])
        self.assertIsNone(result["host"])


class HeaderResourceWidgetTests(TestCase):
    """The pinned header meters (base.html) render on every cpanel page for
    logged-in users, and never for anonymous visitors."""

    def setUp(self):
        gs = GlobalSettings.get_settings()
        gs.setup_completed = True
        gs.save()
        Environment.objects.get_or_create(
            name="default", defaults={"is_default": True, "python_version": "3.12"}
        )
        self.admin = User.objects.create(
            email="admin@example.com", is_superuser=True, is_staff=True
        )
        WorkspaceMembership.ensure(
            self.admin, Workspace.get_default(), role=WorkspaceMembership.ROLE_OWNER
        )

    def test_header_meters_render_for_logged_in_user(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("cpanel:services"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="headerResources"')
        # All three meters plus the JS hooks that populate them
        for element_id in ("hdrCpuBar", "hdrMemBar", "hdrDiskBar"):
            self.assertContains(resp, element_id)
        self.assertContains(resp, reverse("cpanel:system_resources_api"))

    def test_header_meters_absent_for_anonymous(self):
        resp = self.client.get(reverse("cpanel:services"), follow=True)
        self.assertNotContains(resp, 'id="headerResources"')
