"""EKS/Kubernetes client helpers for authenticating to and interacting with EKS clusters."""

import base64
import re

import boto3
from botocore.signers import RequestSigner
from kubernetes import client, config

STS_TOKEN_EXPIRES_IN = 60


def get_bearer_token(cluster_name: str, region: str) -> str:
    """Generate an IAM-based bearer token for EKS cluster authentication.

    Creates a pre-signed STS GetCallerIdentity URL and encodes it as a
    Kubernetes-compatible bearer token with 60-second expiry.

    Args:
        cluster_name: The name of the EKS cluster to authenticate to.
        region: The AWS region where the cluster is located.

    Returns:
        A bearer token string prefixed with 'k8s-aws-v1.' suitable for
        Kubernetes API authentication.
    """
    session = boto3.session.Session(region_name=region)
    sts_client = session.client("sts")
    service_id = sts_client.meta.service_model.service_id

    signer = RequestSigner(
        service_id,
        region,
        "sts",
        "v4",
        session.get_credentials(),
        session.events,
    )

    params = {
        "method": "GET",
        "url": f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
        "body": {},
        "headers": {"x-k8s-aws-id": cluster_name},
        "context": {},
    }

    signed_url = signer.generate_presigned_url(
        params,
        region_name=region,
        expires_in=STS_TOKEN_EXPIRES_IN,
        operation_name="",
    )

    base64_url = base64.urlsafe_b64encode(signed_url.encode("utf-8")).decode("utf-8")
    return "k8s-aws-v1." + re.sub(r"=*", "", base64_url)


def get_k8s_client(cluster_name: str, region: str) -> client.CustomObjectsApi:
    """Create a Kubernetes CustomObjectsApi client authenticated to an EKS cluster.

    Retrieves cluster endpoint and CA data via EKS describe_cluster, generates
    a bearer token, builds a kubeconfig, and returns a configured CustomObjectsApi.

    Args:
        cluster_name: The name of the EKS cluster to connect to.
        region: The AWS region where the cluster is located.

    Returns:
        A configured kubernetes.client.CustomObjectsApi instance.
    """
    eks_client = boto3.client("eks", region_name=region)
    cluster_info = eks_client.describe_cluster(name=cluster_name)

    endpoint = cluster_info["cluster"]["endpoint"]
    ca_data = cluster_info["cluster"]["certificateAuthority"]["data"]

    token = get_bearer_token(cluster_name, region)

    kubeconfig = {
        "apiVersion": "v1",
        "clusters": [
            {
                "name": "cluster1",
                "cluster": {
                    "certificate-authority-data": ca_data,
                    "server": endpoint,
                },
            }
        ],
        "contexts": [
            {
                "name": "context1",
                "context": {"cluster": "cluster1", "user": "user1"},
            }
        ],
        "current-context": "context1",
        "kind": "Config",
        "preferences": {},
        "users": [{"name": "user1", "user": {"token": token}}],
    }

    config.load_kube_config_from_dict(config_dict=kubeconfig)
    k8s_api_client = client.ApiClient()
    return client.CustomObjectsApi(k8s_api_client)
