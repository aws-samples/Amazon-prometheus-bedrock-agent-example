"""Unit tests for the memory adjustment tool."""

import sys
from unittest.mock import MagicMock, patch

# Mock strands before importing the tool
mock_strands = MagicMock()
mock_strands.tool = lambda func: func
sys.modules.setdefault("strands", mock_strands)

from exceptions import ApplicationNotFoundError, ArgoCDAuthError, HelmNotConfiguredError  # noqa: E402
from tools.memory_adjustment import adjust_memory  # noqa: E402


class TestMemoryAdjustmentHappyPath:
    """Tests for memory adjustment success scenarios."""

    @patch("tools.memory_adjustment.validate_action", return_value=None)
    @patch("tools.memory_adjustment.get_argocd_client")
    @patch("tools.memory_adjustment.get_config")
    def test_happy_path_returns_success_message(
        self, mock_config, mock_get_client, mock_validate
    ):
        """Verify successful memory adjustment returns the client's success message."""
        mock_config.return_value = MagicMock(
            region="us-east-1",
            argocd_url="https://argocd.example.com",
            eks_cluster_name="my-cluster",
        )
        mock_client = MagicMock()
        mock_client.patch_resource_memory.return_value = {
            "status": "success",
            "message": "Successfully updated memory resources for my-app: my-pod limits=512Mi, requests=256Mi",
        }
        mock_get_client.return_value = mock_client

        result = adjust_memory(
            app_name="my-app",
            pod_name="my-pod",
            memory_limit="512Mi",
            memory_request="256Mi",
        )

        assert "Successfully updated" in result
        mock_client.patch_resource_memory.assert_called_once_with(
            "my-app", "my-pod", "512Mi", "256Mi"
        )

    @patch("tools.memory_adjustment.validate_action", return_value=None)
    @patch("tools.memory_adjustment.get_argocd_client")
    @patch("tools.memory_adjustment.get_config")
    def test_default_memory_request_is_100mi(
        self, mock_config, mock_get_client, mock_validate
    ):
        """Verify default memory_request of '100Mi' is used when not provided."""
        mock_config.return_value = MagicMock(
            region="us-east-1",
            argocd_url="https://argocd.example.com",
            eks_cluster_name="my-cluster",
        )
        mock_client = MagicMock()
        mock_client.patch_resource_memory.return_value = {
            "status": "success",
            "message": "Successfully updated memory resources for my-app: my-pod limits=512Mi, requests=100Mi",
        }
        mock_get_client.return_value = mock_client

        adjust_memory(app_name="my-app", pod_name="my-pod", memory_limit="512Mi")

        mock_client.patch_resource_memory.assert_called_once_with(
            "my-app", "my-pod", "512Mi", "100Mi"
        )

    @patch("tools.memory_adjustment.validate_action", return_value=None)
    @patch("tools.memory_adjustment.get_argocd_client")
    @patch("tools.memory_adjustment.get_config")
    def test_calls_get_argocd_client_with_config_values(
        self, mock_config, mock_get_client, mock_validate
    ):
        """get_argocd_client is called with argocd_url, eks_cluster_name, and region from config."""
        mock_config.return_value = MagicMock(
            region="us-west-2",
            argocd_url="https://argocd.example.com",
            eks_cluster_name="my-cluster",
        )
        mock_client = MagicMock()
        mock_client.patch_resource_memory.return_value = {
            "status": "success",
            "message": "ok",
        }
        mock_get_client.return_value = mock_client

        adjust_memory(app_name="my-app", pod_name="my-pod", memory_limit="512Mi")

        mock_get_client.assert_called_once_with(
            "https://argocd.example.com", "my-cluster", "us-west-2"
        )


class TestMemoryAdjustmentMissingParams:
    """Tests for missing or empty parameter handling (validated before guardrails/client)."""

    def test_missing_app_name_returns_error(self):
        """Verify empty app_name returns error mentioning 'app_name'."""
        result = adjust_memory(
            app_name="", pod_name="my-pod", memory_limit="512Mi"
        )
        assert "app_name" in result
        assert "Error" in result

    def test_missing_pod_name_returns_error(self):
        """Verify empty pod_name returns error mentioning 'pod_name'."""
        result = adjust_memory(
            app_name="my-app", pod_name="", memory_limit="512Mi"
        )
        assert "pod_name" in result
        assert "Error" in result

    def test_missing_memory_limit_returns_error(self):
        """Verify empty memory_limit returns error mentioning 'memory_limit'."""
        result = adjust_memory(
            app_name="my-app", pod_name="my-pod", memory_limit=""
        )
        assert "memory_limit" in result
        assert "Error" in result


class TestMemoryAdjustmentGuardrails:
    """Tests for guardrail-based denial of the memory_adjustment action."""

    @patch("tools.memory_adjustment.get_argocd_client")
    @patch("tools.memory_adjustment.validate_action")
    def test_denied_action_returns_guardrail_message_without_calling_client(
        self, mock_validate, mock_get_client
    ):
        """When validate_action denies the action, the ArgoCD client is never invoked."""
        mock_validate.return_value = (
            "Action 'memory_adjustment' is not allowed. Permitted actions: diagnostics."
        )

        result = adjust_memory(
            app_name="my-app", pod_name="my-pod", memory_limit="512Mi"
        )

        assert result.startswith("Error:")
        assert "not allowed" in result
        mock_get_client.assert_not_called()

    @patch("tools.memory_adjustment.get_argocd_client")
    @patch("tools.memory_adjustment.validate_action")
    def test_validate_action_called_with_memory_adjustment(
        self, mock_validate, mock_get_client
    ):
        """validate_action is always checked with the 'memory_adjustment' action name."""
        mock_validate.return_value = "denied"

        adjust_memory(app_name="my-app", pod_name="my-pod", memory_limit="512Mi")

        mock_validate.assert_called_once_with("memory_adjustment")


class TestMemoryAdjustmentExceptionHandling:
    """Tests for exception handling from the ArgoCD client."""

    @patch("tools.memory_adjustment.validate_action", return_value=None)
    @patch("tools.memory_adjustment.get_argocd_client")
    @patch("tools.memory_adjustment.get_config")
    def test_application_not_found_returns_error_message(
        self, mock_config, mock_get_client, mock_validate
    ):
        """ApplicationNotFoundError is surfaced as 'Error: {message}'."""
        mock_config.return_value = MagicMock(
            region="us-east-1", argocd_url="https://argocd.example.com", eks_cluster_name="c"
        )
        mock_client = MagicMock()
        mock_client.patch_resource_memory.side_effect = ApplicationNotFoundError(
            "Application my-app not found"
        )
        mock_get_client.return_value = mock_client

        result = adjust_memory(app_name="my-app", pod_name="my-pod", memory_limit="512Mi")

        assert result == "Error: Application my-app not found"

    @patch("tools.memory_adjustment.validate_action", return_value=None)
    @patch("tools.memory_adjustment.get_argocd_client")
    @patch("tools.memory_adjustment.get_config")
    def test_helm_not_configured_returns_error_message(
        self, mock_config, mock_get_client, mock_validate
    ):
        """HelmNotConfiguredError is surfaced as 'Error: {message}'."""
        mock_config.return_value = MagicMock(
            region="us-east-1", argocd_url="https://argocd.example.com", eks_cluster_name="c"
        )
        mock_client = MagicMock()
        mock_client.patch_resource_memory.side_effect = HelmNotConfiguredError(
            "Application kustomize-app is not configured with a Helm source"
        )
        mock_get_client.return_value = mock_client

        result = adjust_memory(app_name="kustomize-app", pod_name="my-pod", memory_limit="512Mi")

        assert result == "Error: Application kustomize-app is not configured with a Helm source"

    @patch("tools.memory_adjustment.validate_action", return_value=None)
    @patch("tools.memory_adjustment.get_argocd_client")
    @patch("tools.memory_adjustment.get_config")
    def test_argocd_auth_error_returns_error_message(
        self, mock_config, mock_get_client, mock_validate
    ):
        """ArgoCDAuthError is surfaced as 'Error: {message}'."""
        mock_config.return_value = MagicMock(
            region="us-east-1", argocd_url="https://argocd.example.com", eks_cluster_name="c"
        )
        mock_client = MagicMock()
        mock_client.patch_resource_memory.side_effect = ArgoCDAuthError(
            "Failed to patch application: boom"
        )
        mock_get_client.return_value = mock_client

        result = adjust_memory(app_name="my-app", pod_name="my-pod", memory_limit="512Mi")

        assert result == "Error: Failed to patch application: boom"

    @patch("tools.memory_adjustment.validate_action", return_value=None)
    @patch("tools.memory_adjustment.get_argocd_client")
    @patch("tools.memory_adjustment.get_config")
    def test_unexpected_exception_returns_generic_message_without_leaking_details(
        self, mock_config, mock_get_client, mock_validate
    ):
        """Unexpected exceptions never leak their raw message to the caller."""
        mock_config.return_value = MagicMock(
            region="us-east-1", argocd_url="https://argocd.example.com", eks_cluster_name="c"
        )
        mock_client = MagicMock()
        mock_client.patch_resource_memory.side_effect = RuntimeError(
            "internal database connection string leaked here"
        )
        mock_get_client.return_value = mock_client

        result = adjust_memory(app_name="my-app", pod_name="my-pod", memory_limit="512Mi")

        assert result == "Error: An unexpected error occurred while processing the request"
        assert "internal database connection string" not in result
