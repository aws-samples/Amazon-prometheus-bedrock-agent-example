"""Unit tests for the ArgoCD client.

The client manages ArgoCD Application custom resources directly through the
Kubernetes API (IAM-authenticated), bypassing the SSO-protected ArgoCD REST
endpoint on EKS Capability clusters.
"""

from unittest.mock import MagicMock, patch

import pytest

from clients.argocd_client import ArgoCD, get_argocd_client
from exceptions import ApplicationNotFoundError, HelmNotConfiguredError


class TestGetArgoCDClient:
    """Tests for the get_argocd_client factory function."""

    @patch("clients.argocd_client.get_k8s_client")
    @patch("clients.argocd_client.get_config")
    def test_builds_client_from_config_values(self, mock_config, mock_get_k8s):
        """get_argocd_client wires the K8s API client using config's cluster/region."""
        mock_config.return_value = MagicMock(
            eks_cluster_name="my-cluster", region="us-west-2"
        )
        mock_k8s_api = MagicMock()
        mock_get_k8s.return_value = mock_k8s_api

        result = get_argocd_client()

        mock_get_k8s.assert_called_once_with("my-cluster", "us-west-2")
        assert isinstance(result, ArgoCD)
        assert result.api is mock_k8s_api


class TestApplicationExists:
    """Tests for the application_exists / _get_application helpers."""

    def test_returns_true_when_application_found(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.return_value = {"metadata": {"name": "my-app"}}
        client = ArgoCD(mock_api)

        assert client.application_exists("my-app") is True

    def test_returns_false_when_not_found_error_raised(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = Exception("NotFound")
        client = ArgoCD(mock_api)

        assert client.application_exists("ghost-app") is False

    def test_returns_false_when_404_error_raised(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = Exception("(404)\nReason: Not Found")
        client = ArgoCD(mock_api)

        assert client.application_exists("ghost-app") is False

    def test_reraises_unexpected_errors(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = Exception("connection refused")
        client = ArgoCD(mock_api)

        with pytest.raises(Exception, match="connection refused"):
            client.application_exists("my-app")


class TestSyncApplication:
    """Tests for ArgoCD.sync_application."""

    def test_sync_nonexistent_app_raises_not_found(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = Exception("NotFound")
        client = ArgoCD(mock_api)

        with pytest.raises(ApplicationNotFoundError, match="nonexistent-app"):
            client.sync_application("nonexistent-app")

    def test_sync_returns_success_message(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.return_value = {"metadata": {"name": "my-app"}}
        client = ArgoCD(mock_api)

        result = client.sync_application("my-app")

        assert result["status"] == "success"
        assert result["message"] == "Application my-app synced successfully"

    def test_sync_patches_with_hard_refresh_and_replace_strategy(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.return_value = {"metadata": {"name": "my-app"}}
        client = ArgoCD(mock_api)

        client.sync_application("my-app")

        _, kwargs = mock_api.patch_namespaced_custom_object.call_args
        assert kwargs["name"] == "my-app"
        body = kwargs["body"]
        assert body["metadata"]["annotations"]["argocd.argoproj.io/refresh"] == "hard"
        assert body["operation"]["sync"]["syncOptions"] == ["Replace=true", "Force=true"]

    def test_sync_wraps_unexpected_error_as_argocd_auth_error(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.return_value = {"metadata": {"name": "my-app"}}
        mock_api.patch_namespaced_custom_object.side_effect = Exception("api server unreachable")
        client = ArgoCD(mock_api)

        with pytest.raises(Exception) as exc_info:
            client.sync_application("my-app")

        assert "Failed to sync application" in str(exc_info.value)


class TestRollbackApplication:
    """Tests for ArgoCD.rollback_application."""

    def test_rollback_nonexistent_app_raises_not_found(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = Exception("NotFound")
        client = ArgoCD(mock_api)

        with pytest.raises(ApplicationNotFoundError, match="ghost-app"):
            client.rollback_application("ghost-app")

    def test_rollback_insufficient_history_raises_value_error(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.return_value = {
            "metadata": {"name": "my-app"},
            "status": {"history": [{"id": 1, "revision": "abc123"}]},
        }
        client = ArgoCD(mock_api)

        with pytest.raises(ValueError, match="No previous revision"):
            client.rollback_application("my-app")

    def test_rollback_with_sufficient_history_returns_previous_revision(self):
        """rollback_application uses history[-2] (second-to-last entry) as the target revision."""
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.return_value = {
            "metadata": {"name": "my-app"},
            "status": {
                "history": [
                    {"id": 5, "revision": "abc123"},
                    {"id": 4, "revision": "def456"},
                    {"id": 3, "revision": "ghi789"},
                ]
            },
        }
        client = ArgoCD(mock_api)

        result = client.rollback_application("my-app")

        assert result["status"] == "success"
        assert result["revision_id"] == "def456"  # history[-2]
        assert "my-app" in result["message"]

    def test_rollback_wraps_unexpected_error_as_argocd_auth_error(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.return_value = {
            "metadata": {"name": "my-app"},
            "status": {
                "history": [
                    {"id": 5, "revision": "abc123"},
                    {"id": 4, "revision": "def456"},
                ]
            },
        }
        mock_api.patch_namespaced_custom_object.side_effect = Exception("boom")
        client = ArgoCD(mock_api)

        with pytest.raises(Exception) as exc_info:
            client.rollback_application("my-app")

        assert "Failed to rollback application" in str(exc_info.value)


class TestPatchResourceMemory:
    """Tests for ArgoCD.patch_resource_memory."""

    def test_nonexistent_app_raises_not_found(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = Exception("NotFound")
        client = ArgoCD(mock_api)

        with pytest.raises(ApplicationNotFoundError, match="missing-app"):
            client.patch_resource_memory("missing-app", "my-pod", "256Mi", "100Mi")

    def test_non_helm_app_raises_helm_not_configured(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.return_value = {
            "metadata": {"name": "kustomize-app"},
            "spec": {"source": {"repoURL": "https://github.com/example/repo"}},
        }
        client = ArgoCD(mock_api)

        with pytest.raises(HelmNotConfiguredError, match="kustomize-app"):
            client.patch_resource_memory("kustomize-app", "my-pod", "256Mi", "100Mi")

    @patch("clients.argocd_client.time.sleep")
    @patch("clients.argocd_client.k8s_client")
    def test_helm_app_success_returns_message_and_syncs(self, mock_k8s_module, mock_sleep):
        """Successful patch updates Helm params, deletes matching pods, and syncs."""
        mock_core_api = MagicMock()
        mock_core_api.list_namespaced_pod.return_value = MagicMock(items=[])
        mock_k8s_module.CoreV1Api.return_value = mock_core_api

        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.return_value = {
            "metadata": {"name": "my-helm-app"},
            "spec": {
                "source": {"helm": {"parameters": []}},
                "destination": {"namespace": "helm-guestbook"},
            },
        }
        client = ArgoCD(mock_api)

        result = client.patch_resource_memory("my-helm-app", "my-pod-name", "512Mi", "100Mi")

        assert result["status"] == "success"
        assert "my-helm-app" in result["message"]
        assert "512Mi" in result["message"]
        mock_api.patch_namespaced_custom_object.assert_called()
        mock_sleep.assert_called_once_with(2)

    @patch("clients.argocd_client.time.sleep")
    @patch("clients.argocd_client.k8s_client")
    def test_helm_params_use_camel_case_resource_name(self, mock_k8s_module, mock_sleep):
        """Hyphenated pod names are converted to camelCase for the Helm parameter keys."""
        mock_core_api = MagicMock()
        mock_core_api.list_namespaced_pod.return_value = MagicMock(items=[])
        mock_k8s_module.CoreV1Api.return_value = mock_core_api

        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.return_value = {
            "metadata": {"name": "my-helm-app"},
            "spec": {
                "source": {"helm": {"parameters": []}},
                "destination": {"namespace": "helm-guestbook"},
            },
        }
        client = ArgoCD(mock_api)

        client.patch_resource_memory("my-helm-app", "my-pod-name", "512Mi", "100Mi")

        # First patch call is the spec update containing the Helm parameters
        first_call_body = mock_api.patch_namespaced_custom_object.call_args_list[0].kwargs["body"]
        params = first_call_body["spec"]["source"]["helm"]["parameters"]
        param_names = {p["name"] for p in params}
        assert "myPodName.resources.limits.memory" in param_names
        assert "myPodName.resources.requests.memory" in param_names

    def test_patch_wraps_unexpected_error_as_argocd_auth_error(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.return_value = {
            "metadata": {"name": "my-helm-app"},
            "spec": {"source": {"helm": {"parameters": []}}},
        }
        mock_api.patch_namespaced_custom_object.side_effect = Exception("boom")
        client = ArgoCD(mock_api)

        with pytest.raises(Exception) as exc_info:
            client.patch_resource_memory("my-helm-app", "my-pod", "512Mi", "100Mi")

        assert "Failed to patch application" in str(exc_info.value)


class TestToCamelCase:
    """Tests for the ArgoCD._to_camel_case static helper."""

    @pytest.mark.parametrize(
        "hyphenated,expected",
        [
            ("my-pod-name", "myPodName"),
            ("pod", "pod"),
            ("api-gateway", "apiGateway"),
            ("", ""),
        ],
    )
    def test_conversion(self, hyphenated, expected):
        assert ArgoCD._to_camel_case(hyphenated) == expected


class TestFindHelmSource:
    """Tests for the ArgoCD._find_helm_source static helper."""

    def test_finds_helm_in_single_source(self):
        spec = {"source": {"helm": {"parameters": []}}}
        assert ArgoCD._find_helm_source(spec) == {"parameters": []}

    def test_finds_helm_in_multi_source_sources_list(self):
        spec = {
            "sources": [
                {"repoURL": "https://github.com/example/repo"},
                {"helm": {"parameters": [{"name": "x"}]}},
            ]
        }
        assert ArgoCD._find_helm_source(spec) == {"parameters": [{"name": "x"}]}

    def test_returns_none_when_no_helm_source(self):
        spec = {"source": {"repoURL": "https://github.com/example/repo"}}
        assert ArgoCD._find_helm_source(spec) is None

    def test_returns_none_for_empty_spec(self):
        assert ArgoCD._find_helm_source({}) is None
