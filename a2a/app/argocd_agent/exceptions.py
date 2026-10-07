"""Custom exceptions for the ArgoCD Strands Agent."""


class ArgoCDAgentError(Exception):
    """Base exception for all ArgoCD agent errors."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


class ArgoCDAuthError(ArgoCDAgentError):
    """Raised when IAM authentication with the ArgoCD server fails."""
    pass


class ApplicationNotFoundError(ArgoCDAgentError):
    """Raised when the specified ArgoCD application does not exist."""
    pass


class HelmNotConfiguredError(ArgoCDAgentError):
    """Raised when an ArgoCD application is not configured with a Helm source."""
    pass
