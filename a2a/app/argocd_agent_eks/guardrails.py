"""Action guardrails for the ArgoCD agent."""

import os
import logging

logger = logging.getLogger(__name__)

READONLY_ACTIONS = {"diagnostics"}
DEFAULT_ALLOWED_ACTIONS = "restart,rollback,memory_adjustment,diagnostics"


def get_allowed_actions() -> set:
    raw = os.environ.get("ALLOWED_ACTIONS", DEFAULT_ALLOWED_ACTIONS)
    return {a.strip().lower() for a in raw.split(",") if a.strip()}


def validate_action(action: str) -> str | None:
    action_lower = action.strip().lower()
    if action_lower in READONLY_ACTIONS:
        return None
    allowed = get_allowed_actions()
    if action_lower in allowed:
        return None
    logger.warning(f"Action '{action}' DENIED. Allowed: {sorted(allowed)}")
    return (
        f"Action '{action}' is not allowed. "
        f"Permitted actions: {', '.join(sorted(allowed))}. "
        f"Contact your platform administrator to update the allowed actions."
    )
