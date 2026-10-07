"""Restart/Rollback tool for ArgoCD applications.

Provides a Strands Agent tool to restart (sync) or rollback an ArgoCD
application, authenticated via IAM. Actions are validated against the
allowed actions list before execution.
"""

import logging
import time

from strands import tool

from clients.argocd_client import get_argocd_client
from config import get_config
from exceptions import (
    ApplicationNotFoundError,
    ArgoCDAuthError,
)
from guardrails import validate_action

logger = logging.getLogger(__name__)


@tool
def restart_rollback_argocd(app_name: str, action_name: str) -> str:
    """Restart or rollback an ArgoCD application.

    Args:
        app_name: The name of the ArgoCD application to operate on.
        action_name: The action to perform - either "restart" or "rollback".

    Returns:
        A message indicating the result of the operation.
    """
    missing_params = []
    if not app_name or not app_name.strip():
        missing_params.append("app_name")
    if not action_name or not action_name.strip():
        missing_params.append("action_name")

    if missing_params:
        return f"Error: Missing required parameters: {', '.join(missing_params)}"

    valid_actions = ("restart", "rollback")
    if action_name.strip() not in valid_actions:
        return f"Error: Invalid action '{action_name}'. Valid actions: restart, rollback"

    # Validate action is allowed by guardrails
    denial = validate_action(action_name.strip())
    if denial:
        return f"Error: {denial}"

    try:
        config = get_config()
        client = get_argocd_client(config.argocd_url, config.eks_cluster_name, config.region)
        action = action_name.strip()

        if action == "restart":
            client.sync_application(app_name.strip())
            return f"Application {app_name.strip()} synced successfully"

        if action == "rollback":
            result = client.rollback_application(app_name.strip())
            revision_id = result.get("revision_id")
            time.sleep(5)
            client.sync_application(app_name.strip())
            return (
                f"Application {app_name.strip()} rolled back to revision "
                f"{revision_id} and synced successfully"
            )

    except ApplicationNotFoundError as e:
        return f"Error: {e.message}"
    except ArgoCDAuthError as e:
        return f"Error: {e.message}"
    except ValueError as e:
        return f"Error: {str(e)}"
    except Exception:
        logger.exception("Unexpected error in restart_rollback_argocd")
        return "Error: An unexpected error occurred while processing the request"
