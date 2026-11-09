import copy
import os
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from container_deploy import configuration, wait_for_update
from container_power import power

IMAGE = "example.azurecr.io/llm-debate@sha256:" + "a" * 64
RUNTIME = "/subscriptions/test/resourceGroups/demo/providers/Microsoft.ManagedIdentity/userAssignedIdentities/runtime"


class ContainerDeploymentTests(unittest.TestCase):
    @patch("container_deploy.time.sleep")
    @patch("container_deploy.az")
    def test_waits_for_azure_update_before_allowing_start(self, az, sleep):
        az.side_effect = [
            {"properties": {"provisioningState": "InProgress"}},
            {"properties": {"provisioningState": "Succeeded"}},
        ]
        self.assertEqual(wait_for_update("https://example")["properties"]["provisioningState"],
                         "Succeeded")
        sleep.assert_called_once_with(5)
        az.side_effect = None
        az.return_value = {"properties": {"provisioningState": "Failed"}}
        with self.assertRaises(RuntimeError):
            wait_for_update("https://example")
        # A previous failed release must not prevent a corrected release from being applied.
        self.assertEqual(wait_for_update("https://example", allow_failed=True), az.return_value)

    def test_release_uses_persistent_storage_secret_refs_and_one_worker_instance(self):
        current = {"properties": {"configuration": {
            "secrets": [{"name": "app-password", "value": "never-copy-this"}],
            "ingress": {"external": True, "targetPort": 80},
        }}}
        result = configuration(copy.deepcopy(current), IMAGE,
                               "https://example.blob.core.windows.net", RUNTIME, "client")
        properties = result["properties"]
        self.assertNotIn("secrets", properties["configuration"])
        self.assertNotIn("never-copy-this", str(result))
        self.assertEqual(properties["configuration"]["ingress"]["targetPort"], 8000)
        self.assertFalse(properties["configuration"]["ingress"]["allowInsecure"])
        self.assertEqual(properties["template"]["scale"], {"minReplicas": 1, "maxReplicas": 1})
        env = properties["template"]["containers"][0]["env"]
        self.assertIn({"name": "STORAGE_BACKEND", "value": "azure-blob"}, env)
        self.assertIn({"name": "APP_PASSWORD", "secretRef": "app-password"}, env)
        single_provider = configuration(copy.deepcopy(current), IMAGE,
                                        "https://example.blob.core.windows.net", RUNTIME,
                                        "client", {"groq"})
        provider_env = single_provider["properties"]["template"]["containers"][0]["env"]
        self.assertFalse(any(item["name"] == "GEMINI_API_KEY" for item in provider_env))

    def test_rejects_mutable_image_and_non_azure_storage(self):
        for image, url in [("example.azurecr.io/llm-debate:latest",
                            "https://example.blob.core.windows.net"),
                           (IMAGE, "http://example.com")]:
            with self.assertRaises(ValueError):
                configuration({}, image, url, RUNTIME, "client")

    def test_runtime_identity_is_not_duplicated_when_azure_changes_id_case(self):
        current = {
            "identity": {"type": "UserAssigned", "userAssignedIdentities": {
                RUNTIME.lower(): {"clientId": "existing-client-id"},
            }},
            "properties": {"configuration": {"ingress": {"targetPort": 8000}}},
        }
        result = configuration(current, IMAGE, "https://example.blob.core.windows.net",
                               RUNTIME, "client")
        identities = result["identity"]["userAssignedIdentities"]
        self.assertEqual(len(identities), 1)
        self.assertEqual(identities[RUNTIME], {})


class ContainerPowerTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "AZURE_SUBSCRIPTION_ID": "test", "AZURE_RESOURCE_GROUP": "demo",
            "AZURE_CONTAINER_APP": "app",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @patch("container_power.wait_status")
    @patch("container_power.az")
    def test_records_expiry_before_start_and_blocks_start_on_tag_failure(self, az, wait):
        power("start", now=datetime(2026, 11, 9, tzinfo=UTC))
        self.assertIn('2026-11-09T02:00:00+00:00', str(az.call_args_list[0]))
        self.assertIn('/start?', str(az.call_args_list[1]))
        az.reset_mock()
        az.side_effect = RuntimeError("tag write denied")
        with self.assertRaises(RuntimeError):
            power("start", now=datetime(2026, 11, 9, tzinfo=UTC))
        self.assertEqual(az.call_count, 1)

    @patch("container_power.az")
    def test_no_starts_after_semester(self, az):
        with self.assertRaises(ValueError):
            power("start", now=datetime(2027, 1, 1, tzinfo=UTC))
        az.assert_not_called()

    @patch("container_power.az")
    def test_stopping_stopped_app_is_idempotent(self, az):
        az.return_value = {"properties": {"runningStatus": "Stopped"}}
        power("stop")
        self.assertEqual(az.call_count, 1)
        self.assertIn("get", az.call_args.args)

    @patch("container_power.wait_status")
    @patch("container_power.az")
    def test_reap_stops_expired_and_missing_expiry_but_preserves_live_session(self, az, wait):
        for expiry, stops in [("2026-11-09T02:00:00+00:00", True),
                              ("2026-11-09T06:00:00+00:00", False), (None, True)]:
            az.reset_mock()
            az.return_value = {"tags": {"demo-expires-at": expiry},
                               "properties": {"runningStatus": "Running"}}
            power("reap", now=datetime(2026, 11, 9, 3, tzinfo=UTC))
            self.assertEqual(az.call_count, 2 if stops else 1)


if __name__ == "__main__":
    unittest.main()
