"""Start/stop one existing Container App; reap expired demo sessions."""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

API = "2025-07-01"
TAG_API = "2021-04-01"
DEADLINE_TAG = "demo-expires-at"
SEMESTER_END = datetime(2027, 1, 1, tzinfo=UTC)


def az(*args):
    result = subprocess.run(
        ["az", *args, "--output", "json", "--only-show-errors"],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def resource_url():
    from urllib.parse import quote

    values = [os.environ[key] for key in (
        "AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "AZURE_CONTAINER_APP"
    )]
    subscription, group, app = [quote(value, safe="") for value in values]
    return (f"https://management.azure.com/subscriptions/{subscription}/resourceGroups/{group}"
            f"/providers/Microsoft.App/containerApps/{app}")


def wait_status(url, target):
    for _ in range(90):
        state = az("rest", "--method", "get", "--url", f"{url}?api-version={API}")
        if state["properties"].get("runningStatus") == target:
            return state
        time.sleep(5)
    raise RuntimeError(f"Container App did not reach {target}")


def power(action, *, now=None):
    now = now or datetime.now(UTC)
    url = resource_url()
    if action == "start":
        if now >= SEMESTER_END:
            raise ValueError("Demo hosting ended on 31 December 2026")
        deadline = min(now + timedelta(hours=2), SEMESTER_END)
        # Record expiration before starting. A failed tag update blocks start.
        az("rest", "--method", "patch", "--url",
           f"{url}/providers/Microsoft.Resources/tags/default?api-version={TAG_API}",
           "--body", json.dumps({"operation": "Merge", "properties": {
               "tags": {DEADLINE_TAG: deadline.isoformat()}
           }}))
    elif action == "reap":
        state = az("rest", "--method", "get", "--url", f"{url}?api-version={API}")
        raw = state.get("tags", {}).get(DEADLINE_TAG)
        # Missing/invalid expiry on an enabled app fails closed.
        try:
            deadline = datetime.fromisoformat(raw) if raw else None
            expired = deadline is None or deadline <= now or now >= SEMESTER_END
        except (ValueError, TypeError):
            expired = True
        if not expired or state["properties"].get("runningStatus") == "Stopped":
            return
        action = "stop"
    elif action != "stop":
        raise ValueError("Expected start, stop, or reap")
    az("rest", "--method", "post", "--url", f"{url}/{action}?api-version={API}")
    wait_status(url, "Running" if action == "start" else "Stopped")
    print(f"Container App {action} completed.")
    if action == "start":
        print(f"Session expires at {deadline.isoformat()}; scheduled cleanup is best effort.")


if __name__ == "__main__":
    power(sys.argv[1])
