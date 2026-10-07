"""Unit tests for the K8sGPT diagnostics tool."""

import json
import sys
from unittest.mock import MagicMock, patch

# Mock strands before importing the tool
mock_strands = MagicMock()
mock_strands.tool = lambda func: func
sys.modules.setdefault("strands", mock_strands)

from tools.k8sgpt_diagnostics import get_k8sgpt_diagnostics  # noqa: E402


class TestK8sGPTHappyPath:
    """Tests for K8sGPT diagnostics success scenarios."""

    @patch("tools.k8sgpt_diagnostics.get_k8s_client")
    @patch("tools.k8sgpt_diagnostics.get_config")
    def test_empty_results_returns_empty_list(self, mock_config, mock_k8s_client):
        """Verify empty K8sGPT results returns '[]'."""
        mock_config.return_value = MagicMock(region="us-east-1")
        mock_api = MagicMock()
        mock_api.list_namespaced_custom_object.return_value = {"items": []}
        mock_k8s_client.return_value = mock_api

        result = get_k8sgpt_diagnostics(eks_cluster="my-cluster")

        assert result == "[]"
        parsed = json.loads(result)
        assert parsed == []

    @patch("tools.k8sgpt_diagnostics.get_k8s_client")
    @patch("tools.k8sgpt_diagnostics.get_config")
    def test_results_with_items_returns_json(self, mock_config, mock_k8s_client):
        """Verify K8sGPT results with items returns proper JSON list."""
        mock_config.return_value = MagicMock(region="us-east-1")
        mock_api = MagicMock()
        mock_api.list_namespaced_custom_object.return_value = {
            "items": [
                {
                    "spec": {
                        "name": "pod-xyz",
                        "kind": "Pod",
                        "details": "CrashLoopBackOff detected",
                        "error": [
                            {"text": "Container exited with code 137"}
                        ],
                    }
                },
                {
                    "spec": {
                        "name": "deploy-abc",
                        "kind": "Deployment",
                        "details": "Replica mismatch",
                        "error": [
                            {"text": "Desired 3 but have 1"},
                            {"text": "Scaling timeout"},
                        ],
                    }
                },
            ]
        }
        mock_k8s_client.return_value = mock_api

        result = get_k8sgpt_diagnostics(eks_cluster="my-cluster")

        parsed = json.loads(result)
        assert len(parsed) == 2
        assert parsed[0]["name"] == "pod-xyz"
        assert parsed[0]["kind"] == "Pod"
        assert parsed[0]["details"] == "CrashLoopBackOff detected"
        assert parsed[0]["error_texts"] == ["Container exited with code 137"]
        assert parsed[1]["name"] == "deploy-abc"
        assert parsed[1]["error_texts"] == ["Desired 3 but have 1", "Scaling timeout"]


class TestK8sGPTMissingParams:
    """Tests for missing eks_cluster parameter falling back to config."""

    @patch("tools.k8sgpt_diagnostics.get_k8s_client")
    @patch("tools.k8sgpt_diagnostics.get_config")
    def test_missing_eks_cluster_falls_back_to_config_default(
        self, mock_config, mock_k8s_client
    ):
        """Empty eks_cluster falls back to config.eks_cluster_name, not an error."""
        mock_config.return_value = MagicMock(
            region="us-east-1", eks_cluster_name="default-cluster"
        )
        mock_api = MagicMock()
        mock_api.list_namespaced_custom_object.return_value = {"items": []}
        mock_k8s_client.return_value = mock_api

        result = get_k8sgpt_diagnostics(eks_cluster="")

        assert result == "[]"
        mock_k8s_client.assert_called_once_with("default-cluster", "us-east-1")

    @patch("tools.k8sgpt_diagnostics.get_k8s_client")
    @patch("tools.k8sgpt_diagnostics.get_config")
    def test_whitespace_only_eks_cluster_falls_back_to_config_default(
        self, mock_config, mock_k8s_client
    ):
        """Whitespace-only eks_cluster is treated the same as empty."""
        mock_config.return_value = MagicMock(
            region="us-east-1", eks_cluster_name="default-cluster"
        )
        mock_api = MagicMock()
        mock_api.list_namespaced_custom_object.return_value = {"items": []}
        mock_k8s_client.return_value = mock_api

        get_k8sgpt_diagnostics(eks_cluster="   ")

        mock_k8s_client.assert_called_once_with("default-cluster", "us-east-1")

    @patch("tools.k8sgpt_diagnostics.get_k8s_client")
    @patch("tools.k8sgpt_diagnostics.get_config")
    def test_provided_eks_cluster_overrides_config_default(
        self, mock_config, mock_k8s_client
    ):
        """An explicitly provided eks_cluster takes precedence over the config default."""
        mock_config.return_value = MagicMock(
            region="us-east-1", eks_cluster_name="default-cluster"
        )
        mock_api = MagicMock()
        mock_api.list_namespaced_custom_object.return_value = {"items": []}
        mock_k8s_client.return_value = mock_api

        get_k8sgpt_diagnostics(eks_cluster="explicit-cluster")

        mock_k8s_client.assert_called_once_with("explicit-cluster", "us-east-1")


class TestK8sGPTExceptionHandling:
    """Tests for exception handling."""

    @patch("tools.k8sgpt_diagnostics.get_k8s_client")
    @patch("tools.k8sgpt_diagnostics.get_config")
    def test_exception_returns_error_message(self, mock_config, mock_k8s_client):
        """Verify exception during K8s API call returns error message."""
        mock_config.return_value = MagicMock(region="us-east-1")
        mock_k8s_client.side_effect = Exception("Connection timed out")

        result = get_k8sgpt_diagnostics(eks_cluster="my-cluster")

        assert "Error" in result
        assert "Connection timed out" in result
