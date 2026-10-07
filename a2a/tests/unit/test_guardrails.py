"""Unit tests for the action guardrails module."""

import pytest

from guardrails import (
    DEFAULT_ALLOWED_ACTIONS,
    MUTATING_ACTIONS,
    READONLY_ACTIONS,
    get_allowed_actions,
    is_action_allowed,
    validate_action,
)


@pytest.fixture(autouse=True)
def clear_allowed_actions_env(monkeypatch):
    """Ensure ALLOWED_ACTIONS is unset by default for every test."""
    monkeypatch.delenv("ALLOWED_ACTIONS", raising=False)


class TestGetAllowedActions:
    """Tests for get_allowed_actions()."""

    def test_returns_default_actions_when_env_unset(self):
        expected = {a.strip().lower() for a in DEFAULT_ALLOWED_ACTIONS.split(",")}
        assert get_allowed_actions() == expected

    def test_reads_from_allowed_actions_env_var(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_ACTIONS", "restart,diagnostics")
        assert get_allowed_actions() == {"restart", "diagnostics"}

    def test_normalizes_case_and_whitespace(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_ACTIONS", " Restart , ROLLBACK ,diagnostics")
        assert get_allowed_actions() == {"restart", "rollback", "diagnostics"}

    def test_ignores_empty_entries(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_ACTIONS", "restart,,diagnostics,")
        assert get_allowed_actions() == {"restart", "diagnostics"}

    def test_empty_env_var_results_in_no_allowed_actions(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_ACTIONS", "")
        assert get_allowed_actions() == set()


class TestIsActionAllowed:
    """Tests for is_action_allowed()."""

    @pytest.mark.parametrize("action", sorted(READONLY_ACTIONS))
    def test_readonly_actions_always_allowed_even_with_empty_allowlist(
        self, monkeypatch, action
    ):
        monkeypatch.setenv("ALLOWED_ACTIONS", "")
        assert is_action_allowed(action) is True

    @pytest.mark.parametrize("action", sorted(MUTATING_ACTIONS))
    def test_mutating_actions_allowed_by_default(self, action):
        assert is_action_allowed(action) is True

    @pytest.mark.parametrize("action", sorted(MUTATING_ACTIONS))
    def test_mutating_actions_denied_when_not_in_allowlist(self, monkeypatch, action):
        monkeypatch.setenv("ALLOWED_ACTIONS", "diagnostics")
        assert is_action_allowed(action) is False

    def test_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_ACTIONS", "restart")
        assert is_action_allowed("RESTART") is True
        assert is_action_allowed("Restart") is True

    def test_strips_whitespace(self):
        assert is_action_allowed("  restart  ") is True

    def test_unknown_action_denied_when_not_in_allowlist(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_ACTIONS", "restart")
        assert is_action_allowed("delete_everything") is False


class TestValidateAction:
    """Tests for validate_action()."""

    def test_returns_none_for_allowed_action(self):
        assert validate_action("restart") is None

    def test_returns_none_for_readonly_action_regardless_of_allowlist(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_ACTIONS", "")
        assert validate_action("diagnostics") is None

    def test_returns_message_for_denied_action(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_ACTIONS", "diagnostics")

        result = validate_action("rollback")

        assert result is not None
        assert "rollback" in result
        assert "not allowed" in result
        assert "diagnostics" in result

    def test_denial_message_lists_all_permitted_actions(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_ACTIONS", "restart,diagnostics")

        result = validate_action("memory_adjustment")

        assert "restart" in result
        assert "diagnostics" in result
