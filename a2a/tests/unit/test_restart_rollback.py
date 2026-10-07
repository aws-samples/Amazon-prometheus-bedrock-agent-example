"""Unit tests for the restart/rollback tool."""

import sys
from unittest.mock import MagicMock, patch

# Mock strands before importing the tool
mock_strands = MagicMock()
mock_strands.tool = lambda func: func
sys.modules.setdefault("strands", mock_strands)

from exceptions import ApplicationNotFoundError, ArgoCDAuthError  # noqa: E402
from tools.restart_rollback import restart_rollback_argocd  # noqa: E402


class TestRestartHappyPath:
    """Tests for restart action success scenarios."""

    @patch("tools.restart_rollback.validate_action", return_value=None)
    @patch("tools.restart_rollback.get_argocd_client")
    @patch("tools.restart_rollback.get_config")
    def test_restart_happy_path_returns_success_message(
        self, mock_config, mock_get_client, mock_validate
    ):
        """Verify restart returns 'Application {name} synced successfully'."""
        mock_config.return_value = MagicMock(
            region="us-east-1", argocd_url="https://argocd.example.com", eks_cluster_name="c"
        )
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        result = restart_rollback_argocd(app_name="payments-service", action_name="restart")

        assert result == "Application payments-service synced successfully"
        mock_client.sync_application.assert_called_once_with("payments-service")

    @patch("tools.restart_rollback.validate_action", return_value=None)
    @patch("tools.restart_rollback.get_argocd_client")
    @patch("tools.restart_rollback.get_config")
    def test_calls_get_argocd_client_with_config_values(
        self, mock_config, mock_get_client, mock_validate
    ):
        """get_argocd_client is called with argocd_url, eks_cluster_name, and region from config."""
        mock_config.return_value = MagicMock(
            region="us-west-2", argocd_url="https://argocd.example.com", eks_cluster_name="my-cluster"
        )
        mock_get_client.return_value = MagicMock()

        restart_rollback_argocd(app_name="my-app", action_name="restart")

        mock_get_client.assert_called_once_with(
            "https://argocd.example.com", "my-cluster", "us-west-2"
        )


class TestRollbackHappyPath:
    """Tests for rollback action success scenarios."""

    @patch("tools.restart_rollback.time.sleep")
    @patch("tools.restart_rollback.validate_action", return_value=None)
    @patch("tools.restart_rollback.get_argocd_client")
    @patch("tools.restart_rollback.get_config")
    def test_rollback_happy_path_returns_message_with_revision(
        self, mock_config, mock_get_client, mock_validate, mock_sleep
    ):
        """Verify rollback returns message containing app name and revision ID, then syncs."""
        mock_config.return_value = MagicMock(
            region="us-east-1", argocd_url="https://argocd.example.com", eks_cluster_name="c"
        )
        mock_client = MagicMock()
        mock_client.rollback_application.return_value = {
            "status": "success",
            "message": "Application payments-service rolled back successfully",
            "revision_id": "abc123",
        }
        mock_get_client.return_value = mock_client

        result = restart_rollback_argocd(app_name="payments-service", action_name="rollback")

        assert "payments-service" in result
        assert "abc123" in result
        assert "synced successfully" in result
        mock_client.rollback_application.assert_called_once_with("payments-service")
        mock_client.sync_application.assert_called_once_with("payments-service")
        mock_sleep.assert_called_once_with(5)


class TestMissingParameters:
    """Tests for missing or empty parameter handling (validated before guardrails/client)."""

    def test_missing_app_name_returns_error(self):
        """Verify empty app_name returns error mentioning 'app_name'."""
        result = restart_rollback_argocd(app_name="", action_name="restart")
        assert "app_name" in result
        assert "Error" in result

    def test_missing_action_name_returns_error(self):
        """Verify empty action_name returns error mentioning 'action_name'."""
        result = restart_rollback_argocd(app_name="my-app", action_name="")
        assert "action_name" in result
        assert "Error" in result

    def test_both_params_missing_returns_error(self):
        """Verify both empty params returns error mentioning both."""
        result = restart_rollback_argocd(app_name="", action_name="")
        assert "app_name" in result
        assert "action_name" in result
        assert "Error" in result


class TestInvalidAction:
    """Tests for action_name values outside the allowed set."""

    def test_invalid_action_returns_error_listing_valid_actions(self):
        """An action_name that is neither 'restart' nor 'rollback' is rejected."""
        result = restart_rollback_argocd(app_name="my-app", action_name="delete")
        assert result.startswith("Error:")
        assert "restart" in result
        assert "rollback" in result


class TestGuardrails:
    """Tests for guardrail-based denial of restart/rollback actions."""

    @patch("tools.restart_rollback.get_argocd_client")
    @patch("tools.restart_rollback.validate_action")
    def test_denied_action_returns_guardrail_message_without_calling_client(
        self, mock_validate, mock_get_client
    ):
        """When validate_action denies the action, the ArgoCD client is never invoked."""
        mock_validate.return_value = (
            "Action 'restart' is not allowed. Permitted actions: diagnostics."
        )

        result = restart_rollback_argocd(app_name="my-app", action_name="restart")

        assert result.startswith("Error:")
        assert "not allowed" in result
        mock_get_client.assert_not_called()

    @patch("tools.restart_rollback.get_argocd_client")
    @patch("tools.restart_rollback.validate_action")
    def test_validate_action_called_with_stripped_action_name(
        self, mock_validate, mock_get_client
    ):
        """validate_action receives the stripped action name."""
        mock_validate.return_value = "denied"

        restart_rollback_argocd(app_name="my-app", action_name="  rollback  ")

        mock_validate.assert_called_once_with("rollback")


class TestExceptionHandling:
    """Tests for exception handling from the ArgoCD client."""

    @patch("tools.restart_rollback.validate_action", return_value=None)
    @patch("tools.restart_rollback.get_argocd_client")
    @patch("tools.restart_rollback.get_config")
    def test_application_not_found_returns_error_message(
        self, mock_config, mock_get_client, mock_validate
    ):
        """ApplicationNotFoundError is surfaced as 'Error: {message}'."""
        mock_config.return_value = MagicMock(
            region="us-east-1", argocd_url="https://argocd.example.com", eks_cluster_name="c"
        )
        mock_client = MagicMock()
        mock_client.sync_application.side_effect = ApplicationNotFoundError(
            "Application my-app not found"
        )
        mock_get_client.return_value = mock_client

        result = restart_rollback_argocd(app_name="my-app", action_name="restart")

        assert result == "Error: Application my-app not found"

    @patch("tools.restart_rollback.validate_action", return_value=None)
    @patch("tools.restart_rollback.get_argocd_client")
    @patch("tools.restart_rollback.get_config")
    def test_argocd_auth_error_returns_error_message(
        self, mock_config, mock_get_client, mock_validate
    ):
        """ArgoCDAuthError is surfaced as 'Error: {message}'."""
        mock_config.return_value = MagicMock(
            region="us-east-1", argocd_url="https://argocd.example.com", eks_cluster_name="c"
        )
        mock_client = MagicMock()
        mock_client.sync_application.side_effect = ArgoCDAuthError(
            "Failed to sync application: boom"
        )
        mock_get_client.return_value = mock_client

        result = restart_rollback_argocd(app_name="my-app", action_name="restart")

        assert result == "Error: Failed to sync application: boom"

    @patch("tools.restart_rollback.validate_action", return_value=None)
    @patch("tools.restart_rollback.get_argocd_client")
    @patch("tools.restart_rollback.get_config")
    def test_rollback_insufficient_history_returns_value_error_message(
        self, mock_config, mock_get_client, mock_validate
    ):
        """A ValueError (e.g. insufficient rollback history) is surfaced as 'Error: {str(e)}'."""
        mock_config.return_value = MagicMock(
            region="us-east-1", argocd_url="https://argocd.example.com", eks_cluster_name="c"
        )
        mock_client = MagicMock()
        mock_client.rollback_application.side_effect = ValueError(
            "No previous revision available for rollback"
        )
        mock_get_client.return_value = mock_client

        result = restart_rollback_argocd(app_name="my-app", action_name="rollback")

        assert result == "Error: No previous revision available for rollback"

    @patch("tools.restart_rollback.validate_action", return_value=None)
    @patch("tools.restart_rollback.get_argocd_client")
    @patch("tools.restart_rollback.get_config")
    def test_unexpected_exception_returns_generic_message_without_leaking_details(
        self, mock_config, mock_get_client, mock_validate
    ):
        """Unexpected exceptions never leak their raw message to the caller."""
        mock_config.return_value = MagicMock(
            region="us-east-1", argocd_url="https://argocd.example.com", eks_cluster_name="c"
        )
        mock_client = MagicMock()
        mock_client.sync_application.side_effect = RuntimeError(
            "internal database connection string leaked here"
        )
        mock_get_client.return_value = mock_client

        result = restart_rollback_argocd(app_name="my-app", action_name="restart")

        assert result == "Error: An unexpected error occurred while processing the request"
        assert "internal database connection string" not in result
