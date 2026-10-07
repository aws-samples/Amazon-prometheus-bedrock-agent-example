"""Configuration module for ArgoCD Agent (EKS deployment)."""

import os
from dataclasses import dataclass

DEFAULT_REGION = "us-east-1"
DEFAULT_CLUSTER = "ws-eks-1"
# The ArgoCD endpoint is environment-specific and MUST be supplied at runtime
# via the ArgoCD_LoadBalancer_URL environment variable. No default is baked in.
DEFAULT_ARGOCD_URL = ""


@dataclass
class AgentConfig:
    """Configuration for the ArgoCD agent."""

    region: str
    argocd_url: str
    eks_cluster_name: str


def get_config() -> AgentConfig:
    """Load environment variables with sensible defaults."""
    return AgentConfig(
        region=os.environ.get("Region", DEFAULT_REGION),
        argocd_url=os.environ.get("ArgoCD_LoadBalancer_URL", DEFAULT_ARGOCD_URL),
        eks_cluster_name=os.environ.get("EKS_CLUSTER_NAME", DEFAULT_CLUSTER),
    )
