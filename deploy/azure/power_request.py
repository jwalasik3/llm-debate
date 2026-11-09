"""Authorize fixed demo commands without executing issue/comment contents."""

import json
import os
import re
from pathlib import Path


def requested_action(event_name, event, operators, issue_number, ref):
    if ref != "refs/heads/main":
        return ""
    allowed = {name.lower() for name in re.split(r"[\s,]+", operators) if name}
    sender = event.get("sender", {}).get("login", "").lower()
    if not sender or sender not in allowed:
        return ""
    if event_name == "workflow_dispatch":
        action = event.get("inputs", {}).get("action")
        return action if action in {"start", "stop"} else ""
    if event_name == "issue_comment" and event.get("action") == "created":
        issue = event.get("issue", {})
        if "pull_request" in issue or str(issue.get("number", "")) != issue_number:
            return ""
        comment = event.get("comment", {})
        if comment.get("user", {}).get("login", "").lower() != sender:
            return ""
        return {"/demo start": "start", "/demo stop": "stop"}.get(
            comment.get("body", "").strip(), ""
        )
    return ""


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    action = requested_action(
        os.environ["GITHUB_EVENT_NAME"],
        event,
        os.getenv("DEMO_OPERATORS", ""),
        os.getenv("DEMO_CONTROL_ISSUE", ""),
        os.environ["GITHUB_REF"],
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"action={action}\n")
    print(
        f"Authorized demo action: {action}" if action else "No authorized demo command; skipping."
    )


if __name__ == "__main__":
    main()
