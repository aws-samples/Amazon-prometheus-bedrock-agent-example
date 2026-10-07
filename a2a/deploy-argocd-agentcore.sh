#!/usr/bin/env bash
set -eo pipefail
cd "$(dirname "$0")"

# ===========================================================================
# USER CONFIGURATION
# ===========================================================================
# EKS_CLUSTER_NAME and AWS_REGION are read from the environment (EKS_CLUSTER,
# AWS_REGION); they fall back to the workshop defaults only if unset.
EKS_CLUSTER_NAME="${EKS_CLUSTER:-ws-eks-1}"
AWS_REGION="${AWS_REGION:-us-east-1}"
AGENTCORE_ROLE_NAME="${AGENTCORE_ROLE_NAME:-AmazonBedrockAgentCoreSDKRuntime-us-east-1-31cd73ee4f}"
# ACCOUNT_ID is populated dynamically at the start of every run.
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
# ARGOCD_URL is environment-specific — supply it via the ARGOCD_URL env var.
ARGOCD_URL="${ARGOCD_URL:?Set ARGOCD_URL to your ArgoCD endpoint (e.g. https://<id>.eks-capabilities.<region>.amazonaws.com)}"
ALLOWED_ACTIONS="${ALLOWED_ACTIONS:-restart,rollback,memory_adjustment,diagnostics}"  # Comma-separated allowed actions
# ===========================================================================

setup_iam_and_eks() {
  local role_arn="arn:aws:iam::${ACCOUNT_ID}:role/${AGENTCORE_ROLE_NAME}"

  echo "═══ Setting up IAM permissions ═══"

  # EKS + STS permissions
  aws iam put-role-policy \
    --role-name "$AGENTCORE_ROLE_NAME" \
    --policy-name agentcore-eks-access \
    --policy-document "{
      \"Version\": \"2012-10-17\",
      \"Statement\": [
        {
          \"Effect\": \"Allow\",
          \"Action\": [\"eks:DescribeCluster\"],
          \"Resource\": \"arn:aws:eks:${AWS_REGION}:${ACCOUNT_ID}:cluster/${EKS_CLUSTER_NAME}\"
        },
        {
          \"Effect\": \"Allow\",
          \"Action\": [\"sts:GetCallerIdentity\"],
          \"Resource\": \"*\"
        }
      ]
    }" 2>/dev/null && echo "  ✓ IAM policy attached" || echo "  ✓ IAM policy already exists"

  # Secrets Manager permission (for ArgoCD auth token)
  aws iam put-role-policy \
    --role-name "$AGENTCORE_ROLE_NAME" \
    --policy-name agentcore-secrets-access \
    --policy-document "{
      \"Version\": \"2012-10-17\",
      \"Statement\": [
        {
          \"Effect\": \"Allow\",
          \"Action\": [\"secretsmanager:GetSecretValue\"],
          \"Resource\": \"arn:aws:secretsmanager:${AWS_REGION}:${ACCOUNT_ID}:secret:argocd-auth-token*\"
        }
      ]
    }" 2>/dev/null && echo "  ✓ Secrets Manager policy attached" || echo "  ✓ Secrets Manager policy already exists"

  echo "═══ Setting up EKS access entry ═══"

  # Create access entry (ignore error if already exists)
  aws eks create-access-entry \
    --cluster-name "$EKS_CLUSTER_NAME" \
    --principal-arn "$role_arn" \
    --region "$AWS_REGION" 2>/dev/null \
    && echo "  ✓ EKS access entry created" \
    || echo "  ✓ EKS access entry already exists"

  # Associate cluster admin policy
  aws eks associate-access-policy \
    --cluster-name "$EKS_CLUSTER_NAME" \
    --principal-arn "$role_arn" \
    --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy \
    --access-scope type=cluster \
    --region "$AWS_REGION" 2>/dev/null \
    && echo "  ✓ EKS access policy associated" \
    || echo "  ✓ EKS access policy already associated"
}

deploy_agent() {
  local name="$1" entry="$2" mode="$3"
  echo ""
  echo "═══ Deploying: $name ═══"

  # Remove stale config to avoid ResourceNotFoundException on deleted runtimes
  rm -f .bedrock_agentcore.yaml

  agentcore configure -e "$entry" -n "$name" -rf requirements.txt

  local env_flags="--env Region=${AWS_REGION} --env AWS_REGION=${AWS_REGION} --env AWS_DEFAULT_REGION=${AWS_REGION} --env EKS_CLUSTER_NAME=${EKS_CLUSTER_NAME} --env ArgoCD_LoadBalancer_URL=${ARGOCD_URL} --env ALLOWED_ACTIONS=${ALLOWED_ACTIONS}"

  if [[ "$mode" == "--local" ]]; then
    agentcore launch --local -a "$name" $env_flags
  else
    agentcore launch -a "$name" --auto-update-on-conflict $env_flags
  fi

  # If this is an A2A agent, update the protocol to A2A
  if [[ "$name" == *"a2a"* ]]; then
    echo "  Updating protocol to A2A..."
    local rid
    rid=$(aws bedrock-agentcore-control list-agent-runtimes --region "$AWS_REGION" \
      --query "agentRuntimes[?agentRuntimeName=='${name}'].agentRuntimeId | [0]" \
      --output text 2>/dev/null)
    if [[ -n "$rid" && "$rid" != "None" ]]; then
      local artifact role_arn
      artifact=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$rid" --region "$AWS_REGION" \
        --query "agentRuntimeArtifact" --output json 2>/dev/null)
      role_arn=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$rid" --region "$AWS_REGION" \
        --query "roleArn" --output text 2>/dev/null)
      aws bedrock-agentcore-control update-agent-runtime \
        --agent-runtime-id "$rid" \
        --region "$AWS_REGION" \
        --role-arn "$role_arn" \
        --network-configuration networkMode=PUBLIC \
        --agent-runtime-artifact "$artifact" \
        --protocol-configuration serverProtocol=A2A \
        --output text > /dev/null 2>&1 && echo "  ✓ Protocol set to A2A" || echo "  ⚠ Failed to set A2A protocol"
    fi
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
MODE=""
AGENT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent|-a) AGENT="$2"; shift 2;;
    --local) MODE="--local"; shift;;
    --status) agentcore status; exit;;
    --destroy) agentcore destroy; exit;;
    --setup-only) setup_iam_and_eks; exit;;
    *) shift;;
  esac
done

# Always run IAM/EKS setup first
setup_iam_and_eks

case "$AGENT" in
  argocd) deploy_agent argocdagent app/argocd_agent/entrypoint.py "$MODE";;
  argocd-a2a) deploy_agent argocdagenta2a app/argocd_agent/main_a2a.py "$MODE";;
  *)
    deploy_agent argocdagenta2a app/argocd_agent/main_a2a.py "$MODE"
    ;;
esac

echo ""
echo "═══ Done! ═══"

# ---------------------------------------------------------------------------
# Post-deploy: Apply IAM/EKS permissions to each agent's actual runtime role
# ---------------------------------------------------------------------------
echo ""
echo "═══ Configuring runtime role permissions ═══"
for name in argocdagent argocdagenta2a; do
  rid=$(aws bedrock-agentcore-control list-agent-runtimes --region "$AWS_REGION" \
    --query "agentRuntimes[?agentRuntimeName=='${name}'].agentRuntimeId | [0]" \
    --output text 2>/dev/null)
  if [[ -n "$rid" && "$rid" != "None" ]]; then
    runtime_role_arn=$(aws bedrock-agentcore-control get-agent-runtime \
      --agent-runtime-id "$rid" --region "$AWS_REGION" \
      --query "roleArn" --output text 2>/dev/null)
    if [[ -n "$runtime_role_arn" && "$runtime_role_arn" != "None" ]]; then
      runtime_role_name=$(echo "$runtime_role_arn" | awk -F'/' '{print $NF}')
      echo "  $name → $runtime_role_name"

      # EKS + STS permissions
      aws iam put-role-policy \
        --role-name "$runtime_role_name" \
        --policy-name agentcore-eks-access \
        --policy-document "{
          \"Version\": \"2012-10-17\",
          \"Statement\": [
            {
              \"Effect\": \"Allow\",
              \"Action\": [\"eks:DescribeCluster\"],
              \"Resource\": \"arn:aws:eks:${AWS_REGION}:${ACCOUNT_ID}:cluster/${EKS_CLUSTER_NAME}\"
            },
            {
              \"Effect\": \"Allow\",
              \"Action\": [\"sts:GetCallerIdentity\"],
              \"Resource\": \"*\"
            }
          ]
        }" 2>/dev/null && echo "    ✓ EKS policy attached" || echo "    ✓ EKS policy exists"

      # Secrets Manager permission (for ArgoCD token)
      aws iam put-role-policy \
        --role-name "$runtime_role_name" \
        --policy-name agentcore-secrets-access \
        --policy-document "{
          \"Version\": \"2012-10-17\",
          \"Statement\": [
            {
              \"Effect\": \"Allow\",
              \"Action\": [\"secretsmanager:GetSecretValue\"],
              \"Resource\": \"arn:aws:secretsmanager:${AWS_REGION}:${ACCOUNT_ID}:secret:argocd-auth-token*\"
            }
          ]
        }" 2>/dev/null && echo "    ✓ Secrets Manager policy attached" || echo "    ✓ Secrets Manager policy exists"

      # EKS access entry (delete + recreate to ensure propagation)
      aws eks delete-access-entry \
        --cluster-name "$EKS_CLUSTER_NAME" \
        --principal-arn "$runtime_role_arn" \
        --region "$AWS_REGION" 2>/dev/null || true

      sleep 5

      aws eks create-access-entry \
        --cluster-name "$EKS_CLUSTER_NAME" \
        --principal-arn "$runtime_role_arn" \
        --region "$AWS_REGION" 2>/dev/null \
        && echo "    ✓ EKS access entry created" \
        || echo "    ✓ EKS access entry exists"

      aws eks associate-access-policy \
        --cluster-name "$EKS_CLUSTER_NAME" \
        --principal-arn "$runtime_role_arn" \
        --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy \
        --access-scope type=cluster \
        --region "$AWS_REGION" 2>/dev/null \
        && echo "    ✓ EKS access policy associated" \
        || echo "    ✓ EKS access policy already associated"

      echo ""
    fi
  fi
done

echo "═══ All permissions configured ═══"
echo ""
echo "Invoke: agentcore invoke '{\"prompt\": \"Get diagnostics for ${EKS_CLUSTER_NAME}\"}'"
echo "Status: agentcore status"

echo ""
echo "Agent Endpoints:"
echo "────────────────"
for name in argocdagent argocdagenta2a; do
  rid=$(aws bedrock-agentcore-control list-agent-runtimes --region "$AWS_REGION" \
    --query "agentRuntimes[?agentRuntimeName=='${name}'].agentRuntimeId | [0]" \
    --output text 2>/dev/null)
  if [[ -n "$rid" && "$rid" != "None" ]]; then
    AGENT_ARN="arn:aws:bedrock-agentcore:${AWS_REGION}:${ACCOUNT_ID}:runtime/${rid}"
    ENCODED_ARN=$(python3 -c "from urllib.parse import quote; print(quote('${AGENT_ARN}', safe=''))")
    echo "  $name:"
    echo "    ARN:        ${AGENT_ARN}"
    echo "    Invoke:     https://bedrock-agentcore.${AWS_REGION}.amazonaws.com/runtimes/${ENCODED_ARN}/invocations/"
    echo "    A2A Card:   https://bedrock-agentcore.${AWS_REGION}.amazonaws.com/runtimes/${ENCODED_ARN}/invocations/.well-known/agent-card.json"
    echo ""
  fi
done
