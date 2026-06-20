"""
Seam 1 — internal DataStore API + signed per-run token.

These prove the *seam* is correct and ready for the Stage 2 cutover, while the
script-side helper is still untouched (SQLite, byte-for-byte with today). The
exemption tests directly cover the breakage paths the Phase-A upgrade-safety
audit flagged: setup-wizard 302 and SSL-redirect 301 must NOT hit /internal/.
"""

import json
import uuid

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from unittest import mock

from core.models import DataStore, DataStoreEntry
from core.services.datastore_token import (
    mint_datastore_token,
    verify_datastore_token,
)


def _auth(token):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


class DatastoreTokenTests(TestCase):
    """The stateless signed token: round-trips, and fails closed."""

    def test_round_trip(self):
        run_id = uuid.uuid4()
        token = mint_datastore_token(run_id)
        payload = verify_datastore_token(token)
        self.assertEqual(payload, {"run_id": str(run_id)})

    def test_empty_token_returns_none(self):
        self.assertIsNone(verify_datastore_token(""))
        self.assertIsNone(verify_datastore_token(None))

    def test_tampered_token_returns_none(self):
        token = mint_datastore_token(uuid.uuid4())
        self.assertIsNone(verify_datastore_token(token + "x"))

    def test_expired_token_returns_none(self):
        token = mint_datastore_token(uuid.uuid4())
        # max_age=0 -> any non-zero age is already expired.
        self.assertIsNone(verify_datastore_token(token, max_age=0))


class InternalDatastoreAuthTests(TestCase):
    """Loopback + signed-token gate, NOT the public DB token, NOT rate-limited."""

    def setUp(self):
        self.store = DataStore.objects.create(name="auth_store")
        self.url = reverse("internal:resolve_store", args=["auth_store"])
        self.token = mint_datastore_token(uuid.uuid4())

    def test_missing_token_is_401(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_bad_token_is_401(self):
        resp = self.client.get(self.url, **_auth("not-a-real-token"))
        self.assertEqual(resp.status_code, 401)

    def test_non_loopback_is_403(self):
        resp = self.client.get(self.url, REMOTE_ADDR="10.0.0.5", **_auth(self.token))
        self.assertEqual(resp.status_code, 403)

    def test_valid_token_loopback_ok(self):
        resp = self.client.get(self.url, **_auth(self.token))
        self.assertEqual(resp.status_code, 200)


class InternalDatastoreCrudTests(TestCase):
    """Full KV surface the helper needs, all through the ORM (engine-agnostic)."""

    def setUp(self):
        self.store = DataStore.objects.create(name="kv")
        self.token = mint_datastore_token(uuid.uuid4())

    def _entry_url(self):
        return reverse("internal:entry", args=["kv"])

    def _entries_url(self):
        return reverse("internal:entries", args=["kv"])

    def _put(self, key, value):
        return self.client.put(
            self._entry_url(),
            data=json.dumps({"key": key, "value": value}),
            content_type="application/json",
            **_auth(self.token),
        )

    def _get(self, key):
        return self.client.get(f"{self._entry_url()}?key={key}", **_auth(self.token))

    def test_value_round_trips_for_every_json_type(self):
        cases = {
            "a_string": "hello",
            "with_quotes": 'he said "hi"',
            "unicode": "café — ☕",
            "an_int": 42,
            "a_float": 3.14,
            "a_bool": True,
            "a_null": None,
            "a_list": [1, 2, 3],
            "a_dict": {"retries": 3, "nested": {"x": [True, None]}},
        }
        for key, value in cases.items():
            with self.subTest(key=key):
                self.assertEqual(self._put(key, value).status_code, 200)
                resp = self._get(key)
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json()["value"], value)

    def test_put_is_idempotent_upsert(self):
        self._put("k", {"v": 1})
        self._put("k", {"v": 2})  # update in place, not a duplicate
        self.assertEqual(DataStoreEntry.objects.filter(datastore=self.store, key="k").count(), 1)
        self.assertEqual(self._get("k").json()["value"], {"v": 2})

    def test_get_missing_key_is_key_not_found(self):
        resp = self._get("nope")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"]["code"], "KEY_NOT_FOUND")

    def test_get_missing_store_is_store_not_found(self):
        url = reverse("internal:entry", args=["ghost"])
        resp = self.client.get(f"{url}?key=k", **_auth(self.token))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"]["code"], "STORE_NOT_FOUND")

    def test_put_requires_key_and_value(self):
        resp = self.client.put(
            self._entry_url(),
            data=json.dumps({"key": "k"}),  # no "value"
            content_type="application/json",
            **_auth(self.token),
        )
        self.assertEqual(resp.status_code, 400)

    def test_list_entries_ordered_with_count(self):
        self._put("b", 2)
        self._put("a", 1)
        resp = self.client.get(self._entries_url(), **_auth(self.token))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual([e["key"] for e in body["entries"]], ["a", "b"])

    def test_delete_entry_then_missing(self):
        self._put("k", "v")
        resp = self.client.delete(f"{self._entry_url()}?key=k", **_auth(self.token))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["deleted"])
        self.assertEqual(self._get("k").status_code, 404)

    def test_delete_missing_entry_is_key_not_found(self):
        resp = self.client.delete(f"{self._entry_url()}?key=ghost", **_auth(self.token))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"]["code"], "KEY_NOT_FOUND")

    def test_clear_all_entries(self):
        self._put("a", 1)
        self._put("b", 2)
        resp = self.client.delete(self._entries_url(), **_auth(self.token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(DataStoreEntry.objects.filter(datastore=self.store).count(), 0)

    def test_resolve_reports_entry_count(self):
        self._put("a", 1)
        resp = self.client.get(reverse("internal:resolve_store", args=["kv"]), **_auth(self.token))
        self.assertEqual(resp.json()["entry_count"], 1)


class InternalDatastoreExemptionTests(TestCase):
    """The audit's headline gates: request-path guards must not kill the endpoint."""

    def setUp(self):
        DataStore.objects.create(name="exempt_store")
        self.url = reverse("internal:resolve_store", args=["exempt_store"])
        self.token = mint_datastore_token(uuid.uuid4())

    def test_loopback_hosts_always_allowed(self):
        # The settings-time guarantee that an operator narrowing ALLOWED_HOSTS
        # to their domain cannot break the loopback endpoint or the healthcheck.
        self.assertIn("127.0.0.1", settings.ALLOWED_HOSTS)
        self.assertIn("localhost", settings.ALLOWED_HOSTS)

    @mock.patch("core.services.setup_service.SetupService.is_setup_needed", return_value=True)
    def test_setup_wizard_does_not_redirect_internal(self, _mocked):
        # With setup "needed", "/" redirects to /setup/, but /internal/ must not.
        self.assertEqual(self.client.get("/").status_code, 302)
        resp = self.client.get(self.url, **_auth(self.token))
        self.assertEqual(resp.status_code, 200)

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r"^internal/"])
    def test_ssl_redirect_does_not_301_internal(self):
        # A normal path 301s to https; the internal path is exempt.
        self.assertEqual(self.client.get("/").status_code, 301)
        resp = self.client.get(self.url, **_auth(self.token))
        self.assertNotEqual(resp.status_code, 301)
        self.assertEqual(resp.status_code, 200)
