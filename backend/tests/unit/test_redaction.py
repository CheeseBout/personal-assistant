"""Unit tests for the redaction module."""
import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.core.redaction import redact_text, redact_value, contains_secret, REDACTED


# ---------------------------------------------------------------------------
# redact_text tests
# ---------------------------------------------------------------------------

class TestRedactText:
    """Tests for redact_text function."""

    def test_redacts_sk_proj_keys(self):
        text = "My key is sk-proj-abc123def456ghi"
        result = redact_text(text)
        assert "sk-proj-abc123def456ghi" not in result
        assert "sk-[REDACTED]" in result

    def test_redacts_bearer_tokens(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature"
        result = redact_text(text)
        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert "Bearer [REDACTED]" in result

    def test_preserves_normal_text(self):
        text = "This is a normal message with no secrets"
        result = redact_text(text)
        assert result == text

    def test_non_string_returned_as_is(self):
        assert redact_text(None) is None
        assert redact_text("") == ""
        assert redact_text(123) == 123

    def test_redacts_sk_key_variants(self):
        text = "key: sk-abcdefghijklmnop"
        result = redact_text(text)
        assert "sk-[REDACTED]" in result

    def test_redacts_key_value_patterns(self):
        text = "api_key=mysupersecretvalue123"
        result = redact_text(text)
        assert "mysupersecretvalue123" not in result


# ---------------------------------------------------------------------------
# redact_value tests
# ---------------------------------------------------------------------------

class TestRedactValue:
    """Tests for redact_value function."""

    def test_redacts_dict_values_with_sensitive_keys(self):
        data = {"password": "hunter2", "username": "admin"}
        result = redact_value(data)
        assert result["password"] == REDACTED
        assert result["username"] == "admin"

    def test_recursively_handles_nested_structures(self):
        data = {
            "config": {
                "api_key": "secret123",
                "nested": {
                    "token": "tok-abc",
                    "name": "test",
                }
            }
        }
        result = redact_value(data)
        assert result["config"]["api_key"] == REDACTED
        assert result["config"]["nested"]["token"] == REDACTED
        assert result["config"]["nested"]["name"] == "test"

    def test_handles_lists(self):
        data = [{"password": "pw1"}, {"name": "safe"}]
        result = redact_value(data)
        assert result[0]["password"] == REDACTED
        assert result[1]["name"] == "safe"

    def test_non_string_values_preserved(self):
        data = {"count": 42, "active": True, "value": None}
        result = redact_value(data)
        assert result == data

    def test_strings_with_secrets_redacted(self):
        data = {"message": "Use Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"}
        result = redact_value(data)
        assert "Bearer [REDACTED]" in result["message"]

    def test_input_not_mutated(self):
        data = {"password": "hunter2", "name": "admin"}
        original_password = data["password"]
        redact_value(data)
        assert data["password"] == original_password


# ---------------------------------------------------------------------------
# contains_secret tests
# ---------------------------------------------------------------------------

class TestContainsSecret:
    """Tests for contains_secret function."""

    def test_returns_true_for_password_value_pattern(self):
        assert contains_secret("password=hunter2") is True

    def test_returns_false_for_keyword_without_value(self):
        # "reset your password" has the keyword but no = or : value
        assert contains_secret("reset your password") is False

    def test_returns_true_for_sk_key(self):
        assert contains_secret("my key is sk-proj-abc123def456ghi") is True

    def test_returns_false_for_password_management_mention(self):
        # Mentions "password" but no key=value pattern follows
        assert contains_secret("I use Bitwarden for password management") is False

    def test_returns_false_for_empty_string(self):
        assert contains_secret("") is False

    def test_returns_false_for_non_string(self):
        assert contains_secret(None) is False
        assert contains_secret(42) is False

    def test_returns_true_for_bearer_token(self):
        assert contains_secret("Authorization: Bearer eyJtoken123456") is True

    def test_returns_true_for_token_equals_value(self):
        assert contains_secret("token=abc123def456") is True
