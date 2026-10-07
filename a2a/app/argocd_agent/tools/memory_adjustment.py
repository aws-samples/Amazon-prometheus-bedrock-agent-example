"""Memory adjustment tool for ArgoCD applications.

Provides a Strands Agent tool to adjust memory resources (limits and
requests) for Helm-managed ArgoCD applications, authenticated via IAM.
Actions are validated against the allowed actions list before execution.
"""

import logging

from strands import tool

from clients.argocd_client import get_argocd_client
from config import get_config
from exceptions import (
    ApplicationNotFoundError,
    ArgoCDAuthError,
    HelmNotConfiguredError,
)
from guardrails import validate_action

logger = logging.getLogger(__name__)


@tool
def adjust_memory(
    app_name: str, pod_name: str, memory_limit: str, memory_request: str = "100Mi"
) -> str:
    """Adjust memory resources for a Helm-managed ArgoCD application.

    Args:
        app_name: The name of the ArgoCD application.
        pod_name: The name of the pod/resource (hyphenated, e.g. "my-pod-name").
        memory_limit: The memory limit to set (e.g. "512Mi", "1Gi").
        memory_request: The memory request to set. Defaults to "100Mi".

    Returns:
        A message indicating the result of the memory adjustment.
    """
    missing_params = []
    if not app_name or not app_name.strip():
        missing_params.append("app_name")
    if not pod_name or not pod_name.strip():
        missing_params.append("pod_name")
    if not memory_limit or not memory_limit.strip():
        missing_params.append("memory_limit")

    if missing_params:
        return f"Error: Missing required parameters: {', '.join(missing_params)}"

    # Validate action is allowed by guardrails
    denial = validate_action("memory_adjustment")
    if denial:
        return f"Error: {denial}"

    try:
        config = get_config()
        client = get_argocd_client(config.argocd_url, config.eks_cluster_name, config.region)

        result = client.patch_resource_memory(
            app_name.strip(),
            pod_name.strip(),
            memory_limit.strip(),
            memory_request.strip(),
        )
        return result["message"]

    except ApplicationNotFoundError as e:
        return f"Error: {e.message}"
    except HelmNotConfiguredError as e:
        return f"Error: {e.message}"
    except ArgoCDAuthError as e:
        return f"Error: {e.message}"
    except ValueError as e:
        return f"Error: {str(e)}"
    except Exception:
        logger.exception("Unexpected error in adjust_memory")
        return "Error: An unexpected error occurred while processing the request"
