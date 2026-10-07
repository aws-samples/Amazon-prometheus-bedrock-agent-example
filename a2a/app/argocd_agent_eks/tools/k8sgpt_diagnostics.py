"""K8sGPT diagnostics tool."""

import json
import logging
from strands import tool
from ..clients.k8s_client import get_k8s_client
from ..config import get_config

logger = logging.getLogger(__name__)


@tool
def get_k8sgpt_diagnostics(eks_cluster: str = "") -> str:
    """Retrieve K8sGPT diagnostic results from an EKS cluster."""
    try:
        config = get_config()
        cluster_name = eks_cluster.strip() if eks_cluster and eks_cluster.strip() else config.eks_cluster_name
        api = get_k8s_client(cluster_name, config.region)
        results = api.list_namespaced_custom_object(
            group="core.k8sgpt.ai", version="v1alpha1",
            namespace="k8sgpt-operator-system", plural="results",
        )
        curated = [
            {"name": item["spec"].get("name", ""), "kind": item["spec"].get("kind", ""),
             "details": item["spec"].get("details", ""),
             "error_texts": [e.get("text", "") for e in item["spec"].get("error", [])]}
            for item in results.get("items", [])
        ]
        return json.dumps(curated)
    except Exception as e:
        logger.exception("Error retrieving K8sGPT diagnostics")
        return f"Error retrieving K8sGPT diagnostics: {str(e)}"
