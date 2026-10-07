"""Configuration module for ArgoCD Strands Agent.

Loads environment variables for agent operation.
Falls back to defaults for Region and EKS_CLUSTER_NAME.
"""

import os
from dataclasses import dataclass

DEFAULT_REGION = "us-east-1"
DEFAULT_CLUSTER = "ws-eks-1"
# The ArgoCD endpoint is environment-specific and MUST be supplied at runtime
# via the ArgoCD_LoadBalancer_URL environment variable. No default is baked in.
DEFAULT_ARGOCD_URL = ""

REQUIRED_ENV_VARS = []


@dataclass
class AgentConfig:
    """Configuration for the ArgoCD agent, populated from environment variables."""

    region: str
    argocd_url: str
    eks_cluster_name: str


def get_config() -> AgentConfig:
    """Load environment variables with sensible defaults.

    All values have defaults so the agent works even if env vars
    are not propagated by the runtime.

    Returns:
        AgentConfig: A configuration object.
    """
    return AgentConfig(
        region=os.environ.get("Region", DEFAULT_REGION),
        argocd_url=os.environ.get("ArgoCD_LoadBalancer_URL", DEFAULT_ARGOCD_URL),
        eks_cluster_name=os.environ.get("EKS_CLUSTER_NAME", DEFAULT_CLUSTER),
    )
