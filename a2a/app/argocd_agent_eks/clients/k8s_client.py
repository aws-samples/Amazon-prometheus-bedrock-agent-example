"""Kubernetes client for EKS-deployed agent.

When running inside the EKS cluster, uses in-cluster config.
Falls back to IAM-based auth for remote access.
"""

import base64
import logging
import os
import re

import boto3
from botocore.signers import RequestSigner
from kubernetes import client, config

logger = logging.getLogger(__name__)
STS_TOKEN_EXPIRES_IN = 60


def get_bearer_token(cluster_name: str, region: str) -> str:
    """Generate an IAM-based bearer token for EKS cluster authentication."""
    session = boto3.session.Session(region_name=region)
    sts_client = session.client("sts")
    service_id = sts_client.meta.service_model.service_id

    signer = RequestSigner(
        service_id, region, "sts", "v4",
        session.get_credentials(), session.events,
    )

    params = {
        "method": "GET",
        "url": f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
        "body": {},
        "headers": {"x-k8s-aws-id": cluster_name},
        "context": {},
    }

    signed_url = signer.generate_presigned_url(
        params, region_name=region, expires_in=STS_TOKEN_EXPIRES_IN, operation_name="",
    )

    base64_url = base64.urlsafe_b64encode(signed_url.encode("utf-8")).decode("utf-8")
    return "k8s-aws-v1." + re.sub(r"=*", "", base64_url)


def get_k8s_client(cluster_name: str, region: str) -> client.CustomObjectsApi:
    """Create a Kubernetes CustomObjectsApi client.

    Uses in-cluster config when running inside EKS (KUBERNETES_SERVICE_HOST set),
    otherwise falls back to IAM-based remote auth.
    """
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        # Running inside EKS — use in-cluster service account
        logger.info("Using in-cluster Kubernetes config")
        config.load_incluster_config()
        return client.CustomObjectsApi()

    # Running outside the cluster — use IAM bearer token
    logger.info(f"Using IAM auth for cluster {cluster_name}")
    eks_client = boto3.client("eks", region_name=region)
    cluster_info = eks_client.describe_cluster(name=cluster_name)

    endpoint = cluster_info["cluster"]["endpoint"]
    ca_data = cluster_info["cluster"]["certificateAuthority"]["data"]
    token = get_bearer_token(cluster_name, region)

    kubeconfig = {
        "apiVersion": "v1",
        "clusters": [{"name": "cluster1", "cluster": {"certificate-authority-data": ca_data, "server": endpoint}}],
        "contexts": [{"name": "context1", "context": {"cluster": "cluster1", "user": "user1"}}],
        "current-context": "context1",
        "kind": "Config",
        "preferences": {},
        "users": [{"name": "user1", "user": {"token": token}}],
    }

    config.load_kube_config_from_dict(config_dict=kubeconfig)
    return client.CustomObjectsApi()
