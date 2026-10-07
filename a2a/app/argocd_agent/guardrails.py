"""Action guardrails for the ArgoCD agent.

Validates whether a requested action is allowed before execution.
Actions are checked against a configurable allowlist. Read-only
operations (diagnostics) are always permitted.
"""

import os
import logging

logger = logging.getLogger(__name__)

# Actions that modify cluster state — these require validation
MUTATING_ACTIONS = {
    "restart",
    "rollback",
    "memory_adjustment",
}

# Read-only actions — always allowed
READONLY_ACTIONS = {
    "diagnostics",
}

# Default allowed actions (can be overridden via ALLOWED_ACTIONS env var)
# Format: comma-separated list e.g. "restart,rollback,memory_adjustment,diagnostics"
DEFAULT_ALLOWED_ACTIONS = "restart,rollback,memory_adjustment,diagnostics"


def get_allowed_actions() -> set:
    """Get the set of currently allowed actions.

    Reads from ALLOWED_ACTIONS env var (comma-separated).
    Falls back to DEFAULT_ALLOWED_ACTIONS if not set.

    Returns:
        Set of allowed action names.
    """
    raw = os.environ.get("ALLOWED_ACTIONS", DEFAULT_ALLOWED_ACTIONS)
    return {a.strip().lower() for a in raw.split(",") if a.strip()}


def is_action_allowed(action: str) -> bool:
    """Check if an action is allowed.

    Read-only actions are always allowed.
    Mutating actions are checked against the allowlist.

    Args:
        action: The action name to validate (e.g. "restart", "rollback",
                "memory_adjustment", "diagnostics").

    Returns:
        True if the action is allowed, False otherwise.
    """
    action_lower = action.strip().lower()

    # Read-only actions are always permitted
    if action_lower in READONLY_ACTIONS:
        return True

    allowed = get_allowed_actions()
    is_allowed = action_lower in allowed

    if not is_allowed:
        logger.warning(
            f"Action '{action}' is DENIED. Allowed actions: {sorted(allowed)}"
        )

    return is_allowed


def validate_action(action: str) -> str | None:
    """Validate an action and return an error message if denied.

    Args:
        action: The action name to validate.

    Returns:
        None if the action is allowed.
        An error message string if the action is denied.
    """
    if is_action_allowed(action):
        return None

    allowed = get_allowed_actions()
    return (
        f"Action '{action}' is not allowed. "
        f"Permitted actions: {', '.join(sorted(allowed))}. "
        f"Contact your platform administrator to update the allowed actions."
    )
