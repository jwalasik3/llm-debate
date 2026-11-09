"""Update the existing Container App, preserving its identity, secrets and hostname."""

import json
import os
import re
import time
from pathlib import Path

from container_power import API, az, power, resource_url


def wait_for_update(url, *, allow_failed=False):
    for _ in range(120):
        current = az("rest", "--method", "get", "--url", f"{url}?api-version={API}")
        status = current["properties"].get("provisioningState")
        if status == "Succeeded" or (allow_failed and status in {"Failed", "Canceled"}):
            return current
        if status in {"Failed", "Canceled"}:
            raise RuntimeError(f"Container App update {status}; inspect Azure activity logs")
        time.sleep(5)
    raise RuntimeError("Timed out waiting for Container App configuration update")


def configuration(current, image, account_url, runtime_id, runtime_client_id, providers=None):
    providers = {"groq", "gemini"} if providers is None else providers
    if not re.fullmatch(r"[a-z0-9.-]+\.azurecr\.io/llm-debate@sha256:[0-9a-f]{64}", image):
        raise ValueError("An immutable Azure Container Registry image digest is required")
    if not re.fullmatch(r"https://[a-z0-9]{3,24}\.blob\.core\.windows\.net", account_url):
        raise ValueError("Invalid storage account URL")
    properties = current["properties"]
    config = properties["configuration"]
    config.update(activeRevisionsMode="Single")
    config["registries"] = [{"server": image.split("/")[0], "identity": runtime_id}]
    ingress = config["ingress"]
    ingress.update(external=True, targetPort=8000, transport="auto", allowInsecure=False)
    ingress["traffic"] = [{"latestRevision": True, "weight": 100}]
    template = {
        "containers": [{
            "name": "llm-debate", "image": image,
            "resources": {"cpu": 0.25, "memory": "0.5Gi"},
            "env": [
                {"name": "AUTH_REQUIRED", "value": "true"},
                {"name": "FRONTEND_DIST", "value": "/app/static"},
                {"name": "APP_USERNAME", "secretRef": "app-username"},
                {"name": "APP_PASSWORD", "secretRef": "app-password"},
                *[
                    {"name": f"{name.upper()}_API_KEY", "secretRef": f"{name}-api-key"}
                    for name in sorted(providers)
                ],
                {"name": "STORAGE_BACKEND", "value": "azure-blob"},
                {"name": "AZURE_STORAGE_ACCOUNT_URL", "value": account_url},
                {"name": "AZURE_STORAGE_CONTAINER", "value": "debates"},
                {"name": "AZURE_CLIENT_ID", "value": runtime_client_id},
            ],
            "probes": [{
                "type": kind, "httpGet": {"path": "/healthz", "port": 8000},
                "periodSeconds": 10, "failureThreshold": failures,
            } for kind, failures in [("Startup", 30), ("Readiness", 3)]],
        }],
        "scale": {"minReplicas": 1, "maxReplicas": 1},
    }
    # Secrets are never copied to the release file: PATCH preserves existing secrets.
    config.pop("secrets", None)
    identity = current.get("identity") or {"type": "None"}
    identity["type"] = (
        "SystemAssigned,UserAssigned" if "SystemAssigned" in identity["type"] else "UserAssigned"
    )
    # ARM treats resource IDs case-insensitively; GET may change their capitalization.
    identity["userAssignedIdentities"] = {
        key: {} for key in identity.get("userAssignedIdentities", {})
        if key.casefold() != runtime_id.casefold()
    }
    identity["userAssignedIdentities"][runtime_id] = {}
    identity.pop("principalId", None)
    identity.pop("tenantId", None)
    return {"identity": identity, "properties": {"configuration": config, "template": template}}


def main():
    url = resource_url()
    current = wait_for_update(url, allow_failed=True)
    secrets = az("rest", "--method", "post", "--url", f"{url}/listSecrets?api-version={API}")
    available = {secret["name"] for secret in secrets["value"]}
    providers = {name for name in ("groq", "gemini") if f"{name}-api-key" in available}
    if not {"app-username", "app-password"} <= available or not providers:
        raise ValueError("Configure login secrets and at least one provider key in Azure")
    body = configuration(
        current, os.environ["APP_IMAGE"], os.environ["AZURE_STORAGE_ACCOUNT_URL"],
        os.environ["AZURE_RUNTIME_IDENTITY_ID"], os.environ["AZURE_RUNTIME_CLIENT_ID"], providers,
    )
    # Drain/stop the old engine first: it must not run beside a replacement engine.
    power("stop")
    wait_for_update(url, allow_failed=True)
    release = Path(os.environ.get("RUNNER_TEMP", ".")) / "container-release.json"
    release.write_text(json.dumps(body))
    az("rest", "--method", "patch", "--url", f"{url}?api-version={API}",
       "--body", f"@{release}")
    wait_for_update(url)
    power("start")


if __name__ == "__main__":
    main()
