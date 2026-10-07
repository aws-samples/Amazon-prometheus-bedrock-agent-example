from strands import Agent
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
"""


def create_agent() -> Agent:
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[restart_rollback_argocd, adjust_memory, get_k8sgpt_diagnostics],
    )
