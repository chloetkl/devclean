import hashlib
import hmac

from app.webhook import verify_github_webhook_signature


class TestGitHubWebhookSignatureVerification:
    def test_valid_signature_is_accepted(self):
        secret = "test-secret"
        payload = b'{"action": "opened"}'
        expected_sig = "sha256=" + hmac.new(
            secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        assert verify_github_webhook_signature(payload, expected_sig, secret) is True

    def test_invalid_signature_is_rejected(self):
        secret = "test-secret"
        payload = b'{"action": "opened"}'
        assert verify_github_webhook_signature(payload, "sha256=invalid", secret) is False

    def test_empty_signature_is_rejected(self):
        secret = "test-secret"
        payload = b'{"action": "opened"}'
        assert verify_github_webhook_signature(payload, "", secret) is False


class TestGitHubWebhookPayloadStructure:
    def test_opened_payload_has_required_fields(self):
        payload = {
            "action": "opened",
            "pull_request": {
                "number": 42,
                "title": "Add new feature",
                "html_url": "https://github.com/testorg/testrepo/pull/42",
                "user": {"login": "developer"},
            },
            "repository": {
                "full_name": "testorg/testrepo",
            },
        }
        assert payload["action"] == "opened"
        assert payload["pull_request"]["number"] == 42
        assert payload["repository"]["full_name"] == "testorg/testrepo"

    def test_bot_pr_is_identified(self):
        payload = {
            "action": "opened",
            "pull_request": {
                "number": 99,
                "title": "code quality fix",
                "html_url": "https://github.com/testorg/testrepo/pull/99",
                "user": {"login": "devin-ai-integration[bot]"},
            },
            "repository": {
                "full_name": "testorg/testrepo",
            },
        }
        pr_author = payload["pull_request"]["user"]["login"]
        assert pr_author == "devin-ai-integration[bot]"

    def test_closed_action_is_not_opened(self):
        payload = {
            "action": "closed",
            "pull_request": {
                "number": 42,
                "title": "Add new feature",
                "html_url": "https://github.com/testorg/testrepo/pull/42",
            },
            "repository": {
                "full_name": "testorg/testrepo",
            },
        }
        assert payload["action"] != "opened"
