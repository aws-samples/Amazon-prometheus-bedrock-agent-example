"""Kubernetes client for Guardrail Agent."""

import logging
import os

from kubernetes import client, config

logger = logging.getLogger(__name__)


def get_k8s_clients():
    """Return CoreV1Api and PolicyV1Api clients.

    Uses in-cluster config when on EKS, otherwise IAM-based auth.
    """
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        logger.info("Using in-cluster Kubernetes config")
        config.load_incluster_config()
    else:
        logger.info("Using kubeconfig")
        config.load_kube_config()

    return client.CoreV1Api(), client.PolicyV1Api(), client.AppsV1Api()
