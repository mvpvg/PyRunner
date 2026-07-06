"""
Tests for the Brand Tracker plugin.

These run inside the PyRunner repo with the normal Django test runner — the
plugin is developed in-tree, so ``core.plugins.api`` is importable and the SDK is
exercised for real (no fakes). They are imported into the main suite by the thin
shim ``core/test_brand_tracker_plugin.py``, which splices ``examples/`` onto the
``plugins`` package path (exactly as Dev Mode does) so this module loads as
``plugins.brand_tracker.tests`` and the relative imports below resolve.

Coverage: the cross-process worker contract, the worker's pure dedup/retention
helpers (where a bug silently double-reports or never expires data), and
idempotent provisioning through the real SDK. The networked source functions are
verified by real runs, not unit-mocked here.
"""

import json
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase

from . import provisioning as prov
from . import worker_body as wb
from .forms import SECRET_FIELDS, BrandTrackerConfigForm


# --------------------------------------------------------------------------- #
# Cross-process worker contract — secret env names + config keys are wired to the
# standalone worker_body by convention; _worker_code() must fail loudly at Save
# if they drift, never ship a silently misconfigured tracker.
# --------------------------------------------------------------------------- #

class WorkerContractTests(SimpleTestCase):
    def test_shipped_worker_references_every_secret_and_config_key(self):
        code = prov._worker_code()
        for token in list(SECRET_FIELDS.values()) + list(prov.CONFIG_KEYS):
            self.assertIn(token, code, f"worker_body.py is missing reference to {token}")

    def test_drift_raises_loudly(self):
        with mock.patch.object(prov, "CONFIG_KEYS", prov.CONFIG_KEYS + ("zzz_unreferenced",)):
            with self.assertRaises(ValueError) as cm:
                prov._worker_code()
        self.assertIn("zzz_unreferenced", str(cm.exception))


# --------------------------------------------------------------------------- #
# Canonicalization + dedup — THE correctness invariant: the same article via web
# and news (http/https, www/m, amp, utm tags) must collapse to ONE key.
# --------------------------------------------------------------------------- #

class CanonicalUrlTests(SimpleTestCase):
    def test_scheme_host_trailing_and_tracking_collapse(self):
        a = wb.canonical_url("http://www.example.com/Article?utm_source=news&utm_medium=x")
        b = wb.canonical_url("https://example.com/Article/")
        self.assertEqual(a, b)

    def test_amp_and_mobile_host_collapse(self):
        self.assertEqual(
            wb.canonical_url("https://m.example.com/news/amp"),
            wb.canonical_url("https://example.com/news"),
        )

    def test_click_id_params_dropped_but_real_params_kept(self):
        self.assertEqual(
            wb.canonical_url("https://x.com/p?id=42&fbclid=abc&gclid=z"),
            "https://x.com/p?id=42",
        )

    def test_distinct_paths_stay_distinct(self):
        self.assertNotEqual(
            wb.canonical_url("https://example.com/a"),
            wb.canonical_url("https://example.com/b"),
        )

    def test_web_and_news_mention_of_same_article_dedupe(self):
        web = wb._mention("k", "T", "http://www.site.com/story?utm_campaign=a", "s", "web")
        news = wb._mention("k", "T", "https://site.com/story/", "s", "news")
        self.assertEqual(web["canonical"], news["canonical"])


class ExcludedDomainTests(SimpleTestCase):
    def test_exact_and_subdomain_excluded(self):
        self.assertTrue(wb.is_excluded_domain("https://example.com/x", ["example.com"]))
        self.assertTrue(wb.is_excluded_domain("https://blog.example.com/x", ["example.com"]))

    def test_substring_lookalikes_not_excluded(self):
        # The bug the prototype had: substring match wrongly blocks these.
        self.assertFalse(wb.is_excluded_domain("https://notexample.com/x", ["example.com"]))
        self.assertFalse(wb.is_excluded_domain("https://example.com.evil.com/x", ["example.com"]))

    def test_blank_excludes_ignored(self):
        self.assertFalse(wb.is_excluded_domain("https://example.com", ["", "  "]))


class HelperTests(SimpleTestCase):
    def test_matches_keyword(self):
        self.assertTrue(wb.matches_keyword("Foo", "this has FoO inside"))
        self.assertFalse(wb.matches_keyword("Foo", "bar baz"))

    def test_prune_window_drops_old_items(self):
        items = [
            {"found_at": "2026-01-01T00:00:00"},
            {"found_at": "2026-06-20T00:00:00"},
            {"found_at": ""},
        ]
        kept = wb.prune_window(items, "2026-03-01T00:00:00")
        self.assertEqual(kept, [{"found_at": "2026-06-20T00:00:00"}])


# --------------------------------------------------------------------------- #
# AI enrichment — provider gating, JSON parsing, batching/ceiling, and graceful
# degrade. The networked provider call (_classify) is mocked; everything else is
# the real worker logic.
# --------------------------------------------------------------------------- #

class EnrichmentTests(SimpleTestCase):
    def _mentions(self, n):
        return [wb._mention("k", f"T{i}", f"https://x.com/{i}", "s", "web") for i in range(n)]

    def test_parse_tolerates_shapes_and_validates(self):
        arr = wb._parse_classifications('[{"i":0,"source_type":"news","sentiment":"positive"}]')
        self.assertEqual(arr[0], {"source_type": "news", "sentiment": "positive"})
        fenced = wb._parse_classifications('```json\n{"results":[{"i":0,"source_type":"BOGUS","sentiment":"x"}]}\n```')
        self.assertEqual(fenced[0], {"source_type": "other", "sentiment": "neutral"})  # invalid → fallback
        self.assertEqual(wb._parse_classifications("not json at all"), {})

    def test_off_is_a_noop(self):
        with mock.patch.object(wb, "ENRICH_PROVIDER", "off"):
            ms = self._mentions(2)
            wb.enrich_mentions(ms)
        self.assertEqual(ms[0]["source_type"], "")

    def test_applies_tags_in_order(self):
        canned = json.dumps({"results": [
            {"i": 0, "source_type": "blog", "sentiment": "positive"},
            {"i": 1, "source_type": "forum", "sentiment": "negative"},
        ]})
        with mock.patch.object(wb, "ENRICH_PROVIDER", "openrouter"), \
                mock.patch.object(wb, "OPENROUTER_API_KEY", "key"), \
                mock.patch.object(wb, "_classify", return_value=canned):
            ms = self._mentions(2)
            wb.enrich_mentions(ms)
        self.assertEqual(ms[0]["sentiment"], "positive")
        self.assertEqual(ms[1]["source_type"], "forum")

    def test_unavailable_provider_degrades(self):
        with mock.patch.object(wb, "ENRICH_PROVIDER", "openrouter"), \
                mock.patch.object(wb, "OPENROUTER_API_KEY", ""):
            ms = self._mentions(2)
            wb.enrich_mentions(ms)  # must not raise
        self.assertEqual(ms[0]["source_type"], "")

    def test_batch_failure_degrades(self):
        with mock.patch.object(wb, "ENRICH_PROVIDER", "openrouter"), \
                mock.patch.object(wb, "OPENROUTER_API_KEY", "key"), \
                mock.patch.object(wb, "_classify", side_effect=RuntimeError("boom")):
            ms = self._mentions(2)
            wb.enrich_mentions(ms)  # must not raise
        self.assertEqual(ms[0]["source_type"], "")

    def test_per_run_ceiling(self):
        canned = json.dumps({"results": [
            {"i": i, "source_type": "blog", "sentiment": "neutral"} for i in range(wb.ENRICH_BATCH)
        ]})
        with mock.patch.object(wb, "ENRICH_PROVIDER", "openrouter"), \
                mock.patch.object(wb, "OPENROUTER_API_KEY", "key"), \
                mock.patch.object(wb, "_classify", return_value=canned):
            ms = self._mentions(wb.ENRICH_MAX + 20)
            wb.enrich_mentions(ms)
        self.assertEqual(ms[wb.ENRICH_MAX - 1]["source_type"], "blog")   # within ceiling
        self.assertEqual(ms[wb.ENRICH_MAX]["source_type"], "")           # beyond ceiling


# --------------------------------------------------------------------------- #
# Config form — required fields, schedule/time + credit bounds, the email-report
# trio, and the first-setup vs. already-configured credential requirement.
# --------------------------------------------------------------------------- #

class ConfigFormTests(SimpleTestCase):
    ENVS = [SimpleNamespace(name="prod")]

    def _form(self, *, configured=frozenset(), **over):
        data = {
            "keywords": "SimplerLLM\nPyRunner",
            "excluded_domains": "",
            "num_results": "10",
            "news_enabled": "on",
            "serper_api_key": "sk",
            "retention_days": "90",
            "monthly_credit_cap": "0",
            "enrich_provider": "off",
            "environment": "prod",
            "notify_on": "failure",
            "schedule_weekday": "0",
            "schedule_time": "08:00",
            "timezone": "UTC",
        }
        data.update(over)
        return BrandTrackerConfigForm(
            data, environments=self.ENVS, configured_secrets=set(configured)
        )

    def test_valid_form(self):
        self.assertTrue(self._form().is_valid())

    def test_keywords_required(self):
        form = self._form(keywords="   \n  ")
        self.assertFalse(form.is_valid())
        self.assertIn("keywords", form.errors)

    def test_serper_required_on_first_setup(self):
        form = self._form(serper_api_key="")
        self.assertFalse(form.is_valid())
        self.assertIn("serper_api_key", form.errors)

    def test_serper_optional_once_configured(self):
        form = self._form(configured={"SERPER_API_KEY"}, serper_api_key="")
        self.assertTrue(form.is_valid(), form.errors)

    def test_bad_time_rejected(self):
        for bad in ("25:00", "12:60", "0800", "8am"):
            form = self._form(schedule_time=bad)
            self.assertFalse(form.is_valid(), bad)
            self.assertIn("schedule_time", form.errors)

    def test_num_results_bounds(self):
        self.assertFalse(self._form(num_results="0").is_valid())
        self.assertFalse(self._form(num_results="101").is_valid())

    def test_email_report_requires_destination_sender_and_key(self):
        form = self._form(email_enabled="on")
        self.assertFalse(form.is_valid())
        for field in ("email_to", "email_from", "resend_api_key"):
            self.assertIn(field, form.errors)

    def test_email_report_valid_when_complete(self):
        form = self._form(
            email_enabled="on", email_to="me@example.com",
            email_from="alerts@example.com", resend_api_key="re-key",
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_timezone_defaults_to_utc(self):
        form = self._form(timezone="")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["timezone"], "UTC")

    def test_openrouter_enrichment_requires_key_and_model(self):
        form = self._form(enrich_provider="openrouter")
        self.assertFalse(form.is_valid())
        self.assertIn("openrouter_api_key", form.errors)
        self.assertIn("enrich_model", form.errors)

    def test_openrouter_enrichment_valid_when_complete(self):
        form = self._form(
            enrich_provider="openrouter",
            openrouter_api_key="or-key", enrich_model="openai/gpt-4o-mini",
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_claude_enrichment_needs_no_key(self):
        form = self._form(enrich_provider="claude")
        self.assertTrue(form.is_valid(), form.errors)


# --------------------------------------------------------------------------- #
# Provisioning — one Save idempotently creates exactly the declared resources,
# all owned by the plugin slug, through the real SDK.
# --------------------------------------------------------------------------- #

class ProvisionTests(TestCase):
    def setUp(self):
        from core.models import Environment, Workspace

        self.ws = Workspace.get_default()
        self.env = Environment.objects.create(
            name="prod", path="btenv", requirements="requests"
        )
        patch = mock.patch("core.services.schedule_service.ScheduleService.sync_schedule")
        patch.start()
        self.addCleanup(patch.stop)

    def _data(self, **over):
        data = {
            "keywords": "SimplerLLM\nPyRunner",
            "excluded_domains": "learnwithhasan.com",
            "news_enabled": True,
            "hackernews_enabled": True,
            "reddit_enabled": False,
            "num_results": 10,
            "retention_days": 90,
            "monthly_credit_cap": 0,
            "email_enabled": False,
            "email_to": "",
            "email_from": "",
            "serper_api_key": "sk-serper",
            "environment": "prod",
            "notify_on": "failure",
            "notify_email": "",
            "schedule_time": "08:00",
            "schedule_weekday": "0",
            "timezone": "UTC",
        }
        data.update(over)
        return data

    def _counts(self):
        from core.models import DataStore, Script, ScriptSchedule, Secret, SecretGrant

        script = Script.objects.get(owner_plugin=prov.OWNER, owner_key=prov.SCRIPT_KEY)
        return {
            "scripts": Script.objects.filter(owner_plugin=prov.OWNER).count(),
            "secrets": Secret.objects.filter(owner_plugin=prov.OWNER).count(),
            "stores": DataStore.objects.filter(name=f"{prov.OWNER}:{prov.STORE_KEY}").count(),
            "grants": SecretGrant.objects.filter(script=script).count(),
            "schedules": ScriptSchedule.objects.filter(script=script).count(),
        }

    def test_provision_creates_declared_resources(self):
        from core.models import Script

        script, warnings = prov.provision(self._data())
        self.assertEqual(warnings, [])  # env has requests, reddit off
        self.assertEqual(script.name, prov.SCRIPT_NAME)
        self.assertEqual(script.injection_mode, Script.InjectionMode.SELECTED)
        # Only the required Serper secret was supplied.
        self.assertEqual(self._counts(),
                         {"scripts": 1, "secrets": 1, "stores": 1, "grants": 1, "schedules": 1})
        cfg = prov.get_config()
        self.assertEqual(cfg["keywords"], ["SimplerLLM", "PyRunner"])
        self.assertEqual(cfg["excluded_domains"], ["learnwithhasan.com"])
        self.assertEqual(cfg["retention_days"], 90)

    def test_optional_secrets_create_and_grant(self):
        prov.provision(self._data(
            reddit_enabled=True,
            reddit_client_id="rid", reddit_client_secret="rsec",
            email_enabled=True, resend_api_key="re-key",
            email_to="me@example.com", email_from="alerts@example.com",
            enrich_provider="openrouter", enrich_model="openai/gpt-4o-mini",
            openrouter_api_key="or-key",
        ))
        c = self._counts()
        self.assertEqual(c["secrets"], 5)   # serper + 2 reddit + resend + openrouter
        self.assertEqual(c["grants"], 5)
        self.assertEqual(set(prov.configured_secret_keys()), set(SECRET_FIELDS.values()))

    def test_provision_is_idempotent(self):
        prov.provision(self._data())
        prov.provision(self._data(retention_days=30))
        self.assertEqual(self._counts(),
                         {"scripts": 1, "secrets": 1, "stores": 1, "grants": 1, "schedules": 1})
        self.assertEqual(prov.get_config()["retention_days"], 30)

    def test_blank_credential_keeps_existing_value(self):
        from core.models import Secret

        prov.provision(self._data())
        prov.provision(self._data(serper_api_key=""))
        secret = Secret.objects.get(owner_plugin=prov.OWNER, owner_key="SERPER_API_KEY")
        self.assertEqual(secret.get_decrypted_value(), "sk-serper")
        self.assertEqual(Secret.objects.filter(owner_plugin=prov.OWNER).count(), 1)

    def test_environment_missing_requests_warns(self):
        from core.models import Environment

        Environment.objects.create(name="bare", path="bareenv", requirements="")
        _, warnings = prov.provision(self._data(environment="bare"))
        self.assertTrue(any("requests" in w for w in warnings))

    def test_reddit_without_credentials_warns(self):
        _, warnings = prov.provision(self._data(reddit_enabled=True))
        self.assertTrue(any("Reddit" in w for w in warnings))

    def test_unknown_environment_raises(self):
        with self.assertRaises(ValueError):
            prov.provision(self._data(environment="ghost"))

    def test_weekly_schedule_created(self):
        prov.provision(self._data(schedule_weekday="2", schedule_time="09:30"))
        sched = prov.get_schedule()
        self.assertIsNotNone(sched)
        self.assertEqual(sched.run_mode, "weekly")
