import unittest

from power_request import requested_action


class RequestTests(unittest.TestCase):
    def request(self, event, name="issue_comment", ref="refs/heads/main"):
        return requested_action(name, event, "owner, Alice", "12", ref)

    def event(self, body="/demo start", user="alice", number=12):
        return {
            "action": "created",
            "sender": {"login": user},
            "issue": {"number": number},
            "comment": {"body": body, "user": {"login": user}},
        }

    def test_allowlisted_students_can_use_only_exact_commands_on_control_issue(self):
        self.assertEqual(self.request(self.event()), "start")
        self.assertEqual(self.request(self.event(" /demo stop\n")), "stop")
        self.assertEqual(self.request(self.event(user="stranger")), "")
        self.assertEqual(self.request(self.event(number=13)), "")
        self.assertEqual(self.request(self.event("/demo start\necho malicious")), "")

    def test_pr_comments_edits_spoofed_authors_and_other_branches_are_rejected(self):
        event = self.event()
        event["issue"]["pull_request"] = {}
        self.assertEqual(self.request(event), "")
        event = self.event()
        event["action"] = "edited"
        self.assertEqual(self.request(event), "")
        event = self.event()
        event["comment"]["user"]["login"] = "stranger"
        self.assertEqual(self.request(event), "")
        self.assertEqual(self.request(self.event(), ref="refs/heads/other"), "")
        self.assertEqual(
            requested_action("issue_comment", self.event(), "", "12", "refs/heads/main"), ""
        )

    def test_manual_dispatch_also_requires_allowlist_and_fixed_action(self):
        event = {"sender": {"login": "owner"}, "inputs": {"action": "start"}}
        self.assertEqual(self.request(event, "workflow_dispatch"), "start")
        event["inputs"]["action"] = "delete"
        self.assertEqual(self.request(event, "workflow_dispatch"), "")



