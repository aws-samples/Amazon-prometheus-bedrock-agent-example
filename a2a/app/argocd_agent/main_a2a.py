"""ArgoCD Agent — A2A protocol version with skills exposed in agent card.

Runs on port 9000 via Bedrock AgentCore, serves agent card at
/.well-known/agent-card.json with full skill/tool descriptions for A2A discovery.
Connects to ArgoCD installed on EKS using IAM authentication.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strands import Agent
from strands.multiagent.a2a.executor import StrandsA2AExecutor
from bedrock_agentcore.runtime import serve_a2a
from a2a.types import AgentCard, AgentSkill


def get_or_create_agent():
    from tools.restart_rollback import restart_rollback_argocd
    from tools.memory_adjustment import adjust_memory
    from tools.k8sgpt_diagnostics import get_k8sgpt_diagnostics

    SYSTEM_PROMPT = """You are an ArgoCD and Kubernetes operations assistant.
You help platform engineers manage their ArgoCD applications and diagnose Kubernetes cluster issues.

Available operations:
1. **Restart/Rollback** - Restart (sync) or rollback an ArgoCD application
2. **Memory Adjustment** - Adjust memory limits and requests for Helm-managed applications
3. **K8sGPT Diagnostics** - Retrieve diagnostic results from K8sGPT running on an EKS cluster

When a user requests an operation, extract the required parameters from their message
and invoke the appropriate tool. Report results clearly and concisely.

The default EKS cluster is 'ws-eks-1' in us-east-1 unless the user specifies otherwise."""

    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[restart_rollback_argocd, adjust_memory, get_k8sgpt_diagnostics],
    )


# Define skills for the agent card
SKILLS = [
    AgentSkill(
        id="restart-rollback",
        name="Restart or Rollback ArgoCD Application",
        description="Restart (sync) or rollback an ArgoCD application to its previous revision.",
        tags=["argocd", "restart", "rollback", "sync", "deployment"],
        examples=[
            "Restart the frontend application in ArgoCD",
            "Rollback the payments-service application",
        ],
    ),
    AgentSkill(
        id="memory-adjustment",
        name="Adjust Application Memory",
        description="Adjust memory limits and requests for a Helm-managed ArgoCD application.",
        tags=["argocd", "helm", "memory", "resources", "scaling"],
        examples=[
            "Set memory limit to 1Gi for the api-gateway pod in the backend app",
            "Increase memory for redis-cache to 512Mi",
        ],
    ),
    AgentSkill(
        id="k8sgpt-diagnostics",
        name="K8sGPT Cluster Diagnostics",
        description="Retrieve K8sGPT diagnostic results from an EKS cluster. "
        "Returns structured diagnostic information including pod issues, "
        "deployment problems, node health, and resource constraints.",
        tags=["kubernetes", "eks", "diagnostics", "k8sgpt", "cluster-health"],
        examples=[
            "Get diagnostics for the ws-eks-1 cluster",
            "What issues are there in my EKS cluster?",
            "Check cluster health",
        ],
    ),
]


if __name__ == "__main__":
    agent = get_or_create_agent()
    executor = StrandsA2AExecutor(agent)

    agent_card = AgentCard(
        name="ArgoCD Operations Agent",
        description="ArgoCD and Kubernetes operations agent that can restart/rollback "
        "applications, adjust memory resources for Helm-managed apps, and retrieve "
        "K8sGPT diagnostic results from Amazon EKS clusters.",
        url="http://localhost:9000",  # overridden by AGENTCORE_RUNTIME_URL at runtime
        version="1.0.0",
        skills=SKILLS,
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities={
            "streaming": True,
            "pushNotifications": False,
        },
        supportedInterfaces=[
            {
                "url": "http://localhost:9000",
                "protocol": "JSONRPC",
                "protocolVersion": "0.3.0",
            }
        ],
    )

    serve_a2a(executor, agent_card=agent_card)
