# Controlled Self-Healing EKS with AWS DevOps Agent and A2A Remote Agents

This project demonstrates how to extend [AWS DevOps Agent](https://docs.aws.amazon.com/devopsagent/latest/userguide/) with customer-built **remote agents** over the [Agent-to-Agent (A2A) protocol](https://a2a-protocol.org/) to troubleshoot and remediate Amazon EKS workloads under policy-based guardrails.

It contains two customer-built A2A agents:

- **ArgoCD agent** (`app/argocd_agent_eks/`) — Runs K8sGPT diagnostics and performs remediation (memory adjustment, restart, rollback) on ArgoCD-managed applications via Kubernetes CRDs.
- **Guardrail agent** (`app/guardrail_agent/`) — Validates safety conditions (Pod Disruption Budget existence) before any destructive change is allowed.

A third agent (`app/argocd_agent/`) is the Amazon Bedrock AgentCore variant of the ArgoCD agent, deployable with `deploy-argocd-agentcore.sh`.

## Full walkthrough

The end-to-end setup — prerequisites, K8sGPT install, deploying both agents, API Gateway/VPC Link wiring, registering the remote agents with the DevOps Agent, and triggering an investigation — is documented in [`blog/blog-post.md`](blog/blog-post.md). Start there.

## Project structure

```
.
├── app/
│   ├── argocd_agent/        # ArgoCD agent (Bedrock AgentCore variant)
│   ├── argocd_agent_eks/    # ArgoCD agent (EKS/A2A variant)
│   └── guardrail_agent/     # Guardrail (PDB check) agent
├── k8s/                     # Kubernetes manifests for the EKS agents
├── skills/                  # EKS troubleshooting skill for the DevOps Agent
├── lambda/                  # Standalone Lambda implementations (reference)
├── deploy-argocd-eks.sh     # Build/deploy/apigw for the ArgoCD EKS agent
├── deploy-guardrail-agent.sh# Build/deploy for the guardrail agent
├── deploy-argocd-agentcore.sh # Deploy the Bedrock AgentCore ArgoCD agent
├── argocd-application2.yaml # Sample ArgoCD Application for the demo app
├── blog/                    # Blog post walkthrough
├── Dockerfile
├── pyproject.toml
└── requirements.txt
```

## Configuration

All environment-specific values are supplied at runtime via environment variables — no account IDs, cluster names, or endpoints are hardcoded. The common ones:

| Variable | Description |
|----------|-------------|
| `AWS_REGION` | AWS region to operate in (e.g. `us-east-1`) |
| `EKS_CLUSTER` | Target EKS cluster name |
| `ArgoCD_LoadBalancer_URL` | URL of your ArgoCD endpoint |
| `ARGOCD_URL` | Same, for `deploy-argocd-agentcore.sh` (required; no default) |

`ACCOUNT_ID` is resolved dynamically at runtime via `aws sts get-caller-identity`.

## Local development

```bash
# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install the package with dev dependencies
pip install -e ".[dev]"

