"""Unit tests for EKS/Kubernetes client helpers."""

from unittest.mock import MagicMock, patch

import pytest

from clients.k8s_client import get_bearer_token, get_k8s_client


class TestGetBearerToken:
    """Tests for get_bearer_token function."""

    @patch("clients.k8s_client.boto3.session.Session")
    def test_get_bearer_token_returns_prefixed_token(self, mock_session_cls):
        """Token must start with 'k8s-aws-v1.' and have no padding '=' characters."""
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        mock_sts_client = MagicMock()
        mock_sts_client.meta.service_model.service_id = "STS"
        mock_session.client.return_value = mock_sts_client
        mock_session.get_credentials.return_value = MagicMock()
        mock_session.events = MagicMock()

        # Mock the signer to return a known URL
        with patch("clients.k8s_client.RequestSigner") as mock_signer_cls:
            mock_signer = MagicMock()
            mock_signer.generate_presigned_url.return_value = (
                "https://sts.us-east-1.amazonaws.com/?Action=GetCallerIdentity"
            )
            mock_signer_cls.return_value = mock_signer

            token = get_bearer_token("my-cluster", "us-east-1")

        assert token.startswith("k8s-aws-v1.")
        assert "=" not in token

    @patch("clients.k8s_client.boto3.session.Session")
    def test_get_bearer_token_uses_correct_region(self, mock_session_cls):
        """Session must be created with the provided region."""
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        mock_sts_client = MagicMock()
        mock_sts_client.meta.service_model.service_id = "STS"
        mock_session.client.return_value = mock_sts_client
        mock_session.get_credentials.return_value = MagicMock()
        mock_session.events = MagicMock()

        with patch("clients.k8s_client.RequestSigner") as mock_signer_cls:
            mock_signer = MagicMock()
            mock_signer.generate_presigned_url.return_value = "https://sts.eu-west-1.amazonaws.com/?Action=GetCallerIdentity"
            mock_signer_cls.return_value = mock_signer

            get_bearer_token("test-cluster", "eu-west-1")

        mock_session_cls.assert_called_once_with(region_name="eu-west-1")


class TestGetK8sClient:
    """Tests for get_k8s_client function."""

    @patch("clients.k8s_client.client")
    @patch("clients.k8s_client.config")
    @patch("clients.k8s_client.get_bearer_token")
    @patch("clients.k8s_client.boto3.client")
    def test_get_k8s_client_returns_custom_objects_api(
        self, mock_boto3_client, mock_get_token, mock_k8s_config, mock_k8s_client
    ):
        """get_k8s_client must return a CustomObjectsApi instance."""
        mock_eks = MagicMock()
        mock_boto3_client.return_value = mock_eks
        mock_eks.describe_cluster.return_value = {
            "cluster": {
                "endpoint": "https://ABCDEF.eks.amazonaws.com",
                "certificateAuthority": {"data": "dGVzdC1jYS1kYXRh"},
            }
        }
        mock_get_token.return_value = "k8s-aws-v1.fake-token"

        mock_api_client = MagicMock()
        mock_k8s_client.ApiClient.return_value = mock_api_client
        mock_custom_api = MagicMock()
        mock_k8s_client.CustomObjectsApi.return_value = mock_custom_api

        result = get_k8s_client("my-cluster", "us-east-1")

        assert result == mock_custom_api
        mock_k8s_client.CustomObjectsApi.assert_called_once_with(mock_api_client)

    @patch("clients.k8s_client.client")
    @patch("clients.k8s_client.config")
    @patch("clients.k8s_client.get_bearer_token")
    @patch("clients.k8s_client.boto3.client")
    def test_get_k8s_client_calls_describe_cluster(
        self, mock_boto3_client, mock_get_token, mock_k8s_config, mock_k8s_client
    ):
        """describe_cluster must be called with the correct cluster name."""
        mock_eks = MagicMock()
        mock_boto3_client.return_value = mock_eks
        mock_eks.describe_cluster.return_value = {
            "cluster": {
                "endpoint": "https://ABCDEF.eks.amazonaws.com",
                "certificateAuthority": {"data": "dGVzdC1jYS1kYXRh"},
            }
        }
        mock_get_token.return_value = "k8s-aws-v1.fake-token"
        mock_k8s_client.ApiClient.return_value = MagicMock()
        mock_k8s_client.CustomObjectsApi.return_value = MagicMock()

        get_k8s_client("production-cluster", "us-west-2")

        mock_boto3_client.assert_called_once_with("eks", region_name="us-west-2")
        mock_eks.describe_cluster.assert_called_once_with(name="production-cluster")

    @patch("clients.k8s_client.client")
    @patch("clients.k8s_client.config")
    @patch("clients.k8s_client.get_bearer_token")
    @patch("clients.k8s_client.boto3.client")
    def test_get_k8s_client_builds_kubeconfig_with_endpoint_and_ca(
        self, mock_boto3_client, mock_get_token, mock_k8s_config, mock_k8s_client
    ):
        """kubeconfig passed to load_kube_config_from_dict must include endpoint and CA from describe_cluster."""
        test_endpoint = "https://MY-EKS-ENDPOINT.eks.amazonaws.com"
        test_ca_data = "bXktY2EtZGF0YQ=="

        mock_eks = MagicMock()
        mock_boto3_client.return_value = mock_eks
        mock_eks.describe_cluster.return_value = {
            "cluster": {
                "endpoint": test_endpoint,
                "certificateAuthority": {"data": test_ca_data},
            }
        }
        mock_get_token.return_value = "k8s-aws-v1.fake-token"
        mock_k8s_client.ApiClient.return_value = MagicMock()
        mock_k8s_client.CustomObjectsApi.return_value = MagicMock()

        get_k8s_client("test-cluster", "us-east-1")

        # Verify load_kube_config_from_dict was called with a kubeconfig
        # containing the endpoint and CA data
        mock_k8s_config.load_kube_config_from_dict.assert_called_once()
        call_kwargs = mock_k8s_config.load_kube_config_from_dict.call_args
        kubeconfig = call_kwargs.kwargs.get("config_dict") or call_kwargs[1].get("config_dict")

        assert kubeconfig["clusters"][0]["cluster"]["server"] == test_endpoint
        assert kubeconfig["clusters"][0]["cluster"]["certificate-authority-data"] == test_ca_data
        assert kubeconfig["users"][0]["user"]["token"] == "k8s-aws-v1.fake-token"
