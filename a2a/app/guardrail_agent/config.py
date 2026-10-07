"""Configuration for Guardrail Agent."""

import os
from dataclasses import dataclass

DEFAULT_REGION = "us-east-1"
DEFAULT_CLUSTER = "ws-eks-1"


@dataclass
class AgentConfig:
    region: str
    eks_cluster_name: str


def get_config() -> AgentConfig:
    return AgentConfig(
        region=os.environ.get("Region", DEFAULT_REGION),
        eks_cluster_name=os.environ.get("EKS_CLUSTER_NAME", DEFAULT_CLUSTER),
    )
