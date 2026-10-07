"""Custom exceptions for the ArgoCD Agent."""


class ArgoCDAgentError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


class ArgoCDAuthError(ArgoCDAgentError):
    pass


class ApplicationNotFoundError(ArgoCDAgentError):
    pass


class HelmNotConfiguredError(ArgoCDAgentError):
    pass
