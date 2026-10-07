"""K8sGPT diagnostics tool for retrieving Kubernetes cluster diagnostic results.

Provides a Strands Agent tool to query K8sGPT custom resources from an EKS cluster,
returning structured diagnostic information about cluster issues.
"""

import json
import logging

from strands import tool

from clients.k8s_client import get_k8s_client
from config import get_config

logger = logging.getLogger(__name__)


@tool
def get_k8sgpt_diagnostics(eks_cluster: str = "") -> str:
    """Retrieve K8sGPT diagnostic results from an EKS cluster.

    Queries the k8sgpt-operator-system namespace for K8sGPT Result custom
    resources (equivalent to: kubectl get results -n k8sgpt-operator-system).

    Args:
        eks_cluster: The name of the EKS cluster to query. If not provided,
            defaults to the EKS_CLUSTER_NAME environment variable or 'ws-eks-1'.

    Returns:
        A JSON string containing diagnostic results, where each item has
        name, kind, details, and error_texts fields.
    """
    try:
        config = get_config()
        cluster_name = (
            eks_cluster.strip()
            if eks_cluster and eks_cluster.strip()
            else config.eks_cluster_name
        )

        api = get_k8s_client(cluster_name, config.region)

        results = api.list_namespaced_custom_object(
            group="core.k8sgpt.ai",
            version="v1alpha1",
            namespace="k8sgpt-operator-system",
            plural="results",
        )

        curated_results = [
            {
                "name": item["spec"].get("name", ""),
                "kind": item["spec"].get("kind", ""),
                "details": item["spec"].get("details", ""),
                "error_texts": [
                    error.get("text", "")
                    for error in item["spec"].get("error", [])
                ],
            }
            for item in results.get("items", [])
        ]

        return json.dumps(curated_results)

    except Exception as e:
        logger.exception("Error retrieving K8sGPT diagnostics")
        return f"Error retrieving K8sGPT diagnostics: {str(e)}"
