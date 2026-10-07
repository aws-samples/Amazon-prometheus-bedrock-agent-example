#!/usr/bin/env bash
set -eo pipefail
cd "$(dirname "$0")"

# AWS_REGION and EKS_CLUSTER (cluster name) are read from the environment;
# they fall back to the workshop defaults only if unset.
AWS_REGION="${AWS_REGION:-us-east-1}"
EKS_CLUSTER_NAME="${EKS_CLUSTER:-ws-eks-1}"
# ACCOUNT_ID is populated dynamically at the start of every run.
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_REPO_NAME="guardrail-agent"
IMAGE_TAG="${IMAGE_TAG:-latest}"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}"

usage() {
  echo "Usage: $0 [build|deploy|all|status|destroy]"
}

build() {
  echo "═══ Building Guardrail Agent ═══"
  if command -v finch &> /dev/null; then
    RT="finch"; finch vm status 2>/dev/null | grep -q "Running" || finch vm start
  elif command -v docker &> /dev/null; then
    RT="docker"
  else
    echo "Error: No container runtime found"; exit 1
  fi

  aws ecr describe-repositories --repository-names "$ECR_REPO_NAME" --region "$AWS_REGION" > /dev/null 2>&1 \
    || aws ecr create-repository --repository-name "$ECR_REPO_NAME" --region "$AWS_REGION" > /dev/null

  aws ecr get-login-password --region "$AWS_REGION" | $RT login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
  $RT build --platform linux/amd64 -t "${ECR_URI}:${IMAGE_TAG}" -f app/guardrail_agent/Dockerfile .
  $RT push "${ECR_URI}:${IMAGE_TAG}"
  echo "  ✓ Image pushed: ${ECR_URI}:${IMAGE_TAG}"
}

deploy() {
  echo "═══ Deploying Guardrail Agent ═══"

  command -v envsubst > /dev/null 2>&1 || { echo "Error: envsubst not found. Install with: brew install gettext && brew link --force gettext"; exit 1; }

  aws eks update-kubeconfig --name "$EKS_CLUSTER_NAME" --region "$AWS_REGION"
  kubectl apply -f k8s/guardrail-agent/rbac.yaml

  # install.yaml is a template rendered with env values. AGENT_URL is filled
  # in once the shared API Gateway route exists, by k8s/guardrail-agent/setup-apigw.sh
  export ACCOUNT_ID AWS_REGION EKS_CLUSTER_NAME IMAGE_TAG
  export AGENT_URL="${AGENT_URL:-}"
  envsubst '$ACCOUNT_ID $AWS_REGION $EKS_CLUSTER_NAME $IMAGE_TAG $AGENT_URL' \
    < k8s/guardrail-agent/install.yaml | kubectl apply -f -
  kubectl rollout status deployment/guardrail-agent -n guardrail-agent --timeout=120s
  echo "  ✓ Deployed"
  echo ""
  echo "  Test via port-forward:"
  echo "    kubectl port-forward svc/guardrail-agent -n guardrail-agent 9001:9001"
  echo "    curl http://localhost:9001/.well-known/agent-card.json | jq ."
  echo ""
  echo "    curl -X POST http://localhost:9001/ -H 'Content-Type: application/json' \\"
  echo "      -d '{\"jsonrpc\":\"2.0\",\"id\":\"1\",\"method\":\"message/send\",\"params\":{\"message\":{\"role\":\"user\",\"parts\":[{\"kind\":\"text\",\"text\":\"Check PDB for deployment memory-demo in namespace helm-guestbook\"}],\"messageId\":\"test\"}}}'"
}

status() {
  echo "═══ Guardrail Agent Status ═══"
  aws eks update-kubeconfig --name "$EKS_CLUSTER_NAME" --region "$AWS_REGION" > /dev/null 2>&1
  kubectl get pods -n guardrail-agent -l app.kubernetes.io/name=guardrail-agent
}

destroy() {
  echo "═══ Destroying Guardrail Agent ═══"
  kubectl delete -f k8s/guardrail-agent/install.yaml --ignore-not-found
  kubectl delete -f k8s/guardrail-agent/rbac.yaml --ignore-not-found
  echo "  ✓ Done"
}

case "${1:-}" in
  build) build;;
  deploy) deploy;;
  all) build; deploy;;
  status) status;;
  destroy) destroy;;
  *) usage;;
esac
