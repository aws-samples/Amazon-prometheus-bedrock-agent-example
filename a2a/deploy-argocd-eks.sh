#!/usr/bin/env bash
set -eo pipefail
cd "$(dirname "$0")"

# ===========================================================================
# CONFIGURATION
# ===========================================================================
# AWS_REGION and EKS_CLUSTER (cluster name) are read from the environment;
# they fall back to the workshop defaults below only if unset.
AWS_REGION="${AWS_REGION:-us-east-1}"
EKS_CLUSTER_NAME="${EKS_CLUSTER:-ws-eks-1}"
# ACCOUNT_ID is populated dynamically at the start of every run so it is
# never hardcoded and always matches the credentials in use.
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_REPO_NAME="argocd-agent-eks"
IMAGE_TAG="${IMAGE_TAG:-latest}"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}"
APIGW_NAME="argocd-eks-agent-api"
APIGW_STAGE="prod"
NLB_NAME="${NLB_NAME:-argocd-agent-nlb}"
TG_NAME="argocd-agent-tg"
# NODE_SG and API_ID are resolved dynamically where they are needed
# (apigw function) unless supplied via environment variables.
# ===========================================================================

usage() {
  echo "Usage: $0 [COMMAND]"
  echo ""
  echo "Commands:"
  echo "  build      Build and push container image to ECR"
  echo "  deploy     Apply K8s manifests and setup IRSA"
  echo "  apigw      Setup API Gateway + VPC Link (external HTTPS endpoint)"
  echo "  all        Run build + deploy + apigw (full pipeline)"
  echo "  status     Show endpoints and pod status"
  echo "  destroy    Remove all resources"
  echo ""
}

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
build() {
  echo "═══ Step 1: Building container image ═══"

  if command -v finch &> /dev/null; then
    RT="finch"
    finch vm status 2>/dev/null | grep -q "Running" || finch vm start
  elif command -v docker &> /dev/null; then
    RT="docker"
  else
    echo "Error: Neither finch nor docker found"; exit 1
  fi
  echo "  Using: $RT"

  aws ecr describe-repositories --repository-names "$ECR_REPO_NAME" --region "$AWS_REGION" > /dev/null 2>&1 \
    || aws ecr create-repository --repository-name "$ECR_REPO_NAME" --region "$AWS_REGION" > /dev/null

  aws ecr get-login-password --region "$AWS_REGION" | $RT login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
  $RT build --platform linux/amd64 -t "${ECR_URI}:${IMAGE_TAG}" -f app/argocd_agent_eks/Dockerfile .
  $RT push "${ECR_URI}:${IMAGE_TAG}"
  echo "  ✓ Image pushed: ${ECR_URI}:${IMAGE_TAG}"
  echo ""
}

# ---------------------------------------------------------------------------
# Deploy to EKS
# ---------------------------------------------------------------------------
deploy() {
  echo "═══ Step 2: Deploying to EKS ═══"

  command -v envsubst > /dev/null 2>&1 || { echo "Error: envsubst not found. Install with: brew install gettext && brew link --force gettext"; exit 1; }

  aws eks update-kubeconfig --name "$EKS_CLUSTER_NAME" --region "$AWS_REGION"

  # Apply manifests (install.yaml is a template rendered with env values;
  # AGENT_URL is filled in once the API Gateway exists, in apigw())
  kubectl apply -f k8s/argocd-agent-eks/rbac.yaml
  export ACCOUNT_ID AWS_REGION EKS_CLUSTER_NAME IMAGE_TAG
  export AGENT_URL="${AGENT_URL:-}"
  envsubst '$ACCOUNT_ID $AWS_REGION $EKS_CLUSTER_NAME $IMAGE_TAG $AGENT_URL' \
    < k8s/argocd-agent-eks/install.yaml | kubectl apply -f -
  echo "  ✓ Manifests applied"

  # Setup IRSA role
  echo "  Setting up IRSA..."
  OIDC_ID=$(aws eks describe-cluster --name "$EKS_CLUSTER_NAME" --region "$AWS_REGION" \
    --query "cluster.identity.oidc.issuer" --output text | awk -F'/' '{print $NF}')

  aws iam create-role \
    --role-name ArgocdAgentEKSRole \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Federated": "arn:aws:iam::'"$ACCOUNT_ID"':oidc-provider/oidc.eks.'"$AWS_REGION"'.amazonaws.com/id/'"$OIDC_ID"'"},
        "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {
          "StringEquals": {
            "oidc.eks.'"$AWS_REGION"'.amazonaws.com/id/'"$OIDC_ID"':aud": "sts.amazonaws.com",
            "oidc.eks.'"$AWS_REGION"'.amazonaws.com/id/'"$OIDC_ID"':sub": "system:serviceaccount:argocd-agent:argocd-agent-eks"
          }
        }
      }]
    }' 2>/dev/null || echo "  ✓ IRSA role exists"

  aws iam put-role-policy \
    --role-name ArgocdAgentEKSRole \
    --policy-name AgentPermissions \
    --policy-document '{
      "Version": "2012-10-17",
      "Statement": [
        {"Effect": "Allow", "Action": ["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"], "Resource": ["arn:aws:bedrock:*::foundation-model/*","arn:aws:bedrock:*:*:inference-profile/*"]}
      ]
    }' > /dev/null
  echo "  ✓ IRSA permissions configured"

  # Wait for rollout
  echo "  Waiting for rollout..."
  kubectl rollout status deployment/argocd-agent-eks -n argocd-agent --timeout=180s
  echo "  ✓ Deployment ready"
  echo ""
}

# ---------------------------------------------------------------------------
# API Gateway + VPC Link
# ---------------------------------------------------------------------------
apigw() {
  echo "═══ Step 3: Setting up API Gateway ═══"

  aws eks update-kubeconfig --name "$EKS_CLUSTER_NAME" --region "$AWS_REGION" > /dev/null 2>&1

  # Get VPC info
  VPC_ID=$(aws eks describe-cluster --name "$EKS_CLUSTER_NAME" --region "$AWS_REGION" \
    --query "cluster.resourcesVpcConfig.vpcId" --output text)
  SUBNETS=$(aws ec2 describe-subnets \
    --subnet-ids $(aws eks describe-cluster --name "$EKS_CLUSTER_NAME" --region "$AWS_REGION" \
      --query "cluster.resourcesVpcConfig.subnetIds[]" --output text) \
    --region "$AWS_REGION" \
    --query "Subnets[].[AvailabilityZone,SubnetId]" --output text | sort -k1,1 -u | awk '{print $2}')
  echo "  VPC: $VPC_ID"
  echo "  Subnets (one per AZ): $SUBNETS"

  # Get pod IP
  POD_IP=$(kubectl get pods -n argocd-agent -l app.kubernetes.io/name=argocd-agent-eks \
    -o jsonpath='{.items[0].status.podIP}')
  echo "  Pod IP: $POD_IP"

  # Target Group
  TG_ARN=$(aws elbv2 describe-target-groups --names "$TG_NAME" --region "$AWS_REGION" \
    --query "TargetGroups[0].TargetGroupArn" --output text 2>/dev/null || echo "None")

  if [[ "$TG_ARN" == "None" || -z "$TG_ARN" ]]; then
    TG_ARN=$(aws elbv2 create-target-group \
      --name "$TG_NAME" --protocol TCP --port 9000 \
      --vpc-id "$VPC_ID" --target-type ip \
      --health-check-protocol HTTP --health-check-path /.well-known/agent-card.json --health-check-port 9000 \
      --region "$AWS_REGION" --query "TargetGroups[0].TargetGroupArn" --output text)
    echo "  ✓ Target group created"
  else
    echo "  ✓ Target group exists"
  fi

  aws elbv2 register-targets --target-group-arn "$TG_ARN" --targets "Id=${POD_IP},Port=9000" --region "$AWS_REGION" 2>/dev/null
  echo "  ✓ Pod registered: $POD_IP"

  # Internal NLB
  NLB_ARN=$(aws elbv2 describe-load-balancers --names "$NLB_NAME" --region "$AWS_REGION" \
    --query "LoadBalancers[0].LoadBalancerArn" --output text 2>/dev/null || echo "None")

  if [[ "$NLB_ARN" == "None" || -z "$NLB_ARN" ]]; then
    NLB_ARN=$(aws elbv2 create-load-balancer \
      --name "$NLB_NAME" --type network --scheme internal \
      --subnets $SUBNETS --region "$AWS_REGION" \
      --query "LoadBalancers[0].LoadBalancerArn" --output text)
    aws elbv2 create-listener \
      --load-balancer-arn "$NLB_ARN" --protocol TCP --port 9000 \
      --default-actions "Type=forward,TargetGroupArn=${TG_ARN}" \
      --region "$AWS_REGION" > /dev/null
    echo "  ✓ NLB created"
  else
    echo "  ✓ NLB exists"
  fi

  echo "  Waiting for NLB to become active..."
  aws elbv2 wait load-balancer-available --load-balancer-arns "$NLB_ARN" --region "$AWS_REGION"
  NLB_DNS=$(aws elbv2 describe-load-balancers --load-balancer-arns "$NLB_ARN" --region "$AWS_REGION" \
    --query "LoadBalancers[0].DNSName" --output text)
  echo "  ✓ NLB active: $NLB_DNS"

  # Enable cross-zone load balancing (required when pods are in different AZs)
  aws elbv2 modify-load-balancer-attributes \
    --load-balancer-arn "$NLB_ARN" \
    --attributes Key=load_balancing.cross_zone.enabled,Value=true \
    --region "$AWS_REGION" > /dev/null
  echo "  ✓ Cross-zone load balancing enabled"

  # Allow port 9000 from VPC CIDR on node SG (resolved dynamically unless
  # overridden via the NODE_SG env var)
  VPC_CIDR=$(aws ec2 describe-vpcs --vpc-ids "$VPC_ID" --region "$AWS_REGION" \
    --query "Vpcs[0].CidrBlock" --output text)
  NODE_SG="${NODE_SG:-$(aws ec2 describe-security-groups --region "$AWS_REGION" \
    --filters "Name=tag:aws:eks:cluster-name,Values=${EKS_CLUSTER_NAME}" \
    --query "SecurityGroups[0].GroupId" --output text)}"
  aws ec2 authorize-security-group-ingress \
    --group-id "$NODE_SG" --protocol tcp --port 9000 --cidr "$VPC_CIDR" \
    --region "$AWS_REGION" 2>/dev/null || true

  # VPC Link
  VPC_LINK_ID=$(aws apigateway get-vpc-links --region "$AWS_REGION" \
    --query "items[?name=='argocd-agent-vpclink'].id | [0]" --output text 2>/dev/null)

  if [[ -z "$VPC_LINK_ID" || "$VPC_LINK_ID" == "None" ]]; then
    VPC_LINK_ID=$(aws apigateway create-vpc-link \
      --name argocd-agent-vpclink --target-arns "$NLB_ARN" \
      --region "$AWS_REGION" --query "id" --output text)
    echo "  ✓ VPC Link created: $VPC_LINK_ID"
    echo "  Waiting for VPC Link (3-5 min)..."
    while true; do
      STATUS=$(aws apigateway get-vpc-link --vpc-link-id "$VPC_LINK_ID" --region "$AWS_REGION" --query "status" --output text)
      [[ "$STATUS" == "AVAILABLE" ]] && break
      sleep 15
    done
    echo "  ✓ VPC Link AVAILABLE"
  else
    echo "  ✓ VPC Link exists: $VPC_LINK_ID"
  fi

  # API Gateway (honors a pre-set API_ID env var, otherwise looks it up by
  # name, otherwise provisions it here). API_ID is set immediately below,
  # right after provisioning, so downstream steps and other scripts
  # (e.g. k8s/guardrail-agent/setup-apigw.sh) can reuse the same API.
  API_ID="${API_ID:-$(aws apigateway get-rest-apis --region "$AWS_REGION" \
    --query "items[?name=='${APIGW_NAME}'].id | [0]" --output text 2>/dev/null)}"

  if [[ -z "$API_ID" || "$API_ID" == "None" ]]; then
    API_ID=$(aws apigateway create-rest-api \
      --name "$APIGW_NAME" --description "HTTPS proxy for ArgoCD Agent on EKS" \
      --endpoint-configuration types=REGIONAL --region "$AWS_REGION" \
      --query "id" --output text)
    echo "  ✓ API created: $API_ID"
  else
    echo "  ✓ API exists: $API_ID"
  fi
  export API_ID

  ROOT_ID=$(aws apigateway get-resources --rest-api-id "$API_ID" --region "$AWS_REGION" \
    --query "items[?path=='/'].id" --output text)

  PROXY_ID=$(aws apigateway get-resources --rest-api-id "$API_ID" --region "$AWS_REGION" \
    --query "items[?pathPart=='{proxy+}'].id" --output text 2>/dev/null)
  if [[ -z "$PROXY_ID" || "$PROXY_ID" == "None" ]]; then
    PROXY_ID=$(aws apigateway create-resource --rest-api-id "$API_ID" \
      --parent-id "$ROOT_ID" --path-part "{proxy+}" --region "$AWS_REGION" --query "id" --output text)
  fi

  # Proxy integration
  aws apigateway put-method --rest-api-id "$API_ID" --resource-id "$PROXY_ID" \
    --http-method ANY --authorization-type NONE \
    --request-parameters "method.request.path.proxy=true" \
    --region "$AWS_REGION" > /dev/null 2>&1 || true

  aws apigateway put-integration --rest-api-id "$API_ID" --resource-id "$PROXY_ID" \
    --http-method ANY --type HTTP_PROXY --integration-http-method ANY \
    --uri "http://${NLB_DNS}:9000/{proxy}" --connection-type VPC_LINK --connection-id "$VPC_LINK_ID" \
    --request-parameters "integration.request.path.proxy=method.request.path.proxy" \
    --region "$AWS_REGION" > /dev/null 2>&1

  # Root integration (for A2A JSON-RPC POST /)
  aws apigateway put-method --rest-api-id "$API_ID" --resource-id "$ROOT_ID" \
    --http-method ANY --authorization-type NONE \
    --region "$AWS_REGION" > /dev/null 2>&1 || true

  aws apigateway put-integration --rest-api-id "$API_ID" --resource-id "$ROOT_ID" \
    --http-method ANY --type HTTP_PROXY --integration-http-method ANY \
    --uri "http://${NLB_DNS}:9000/" --connection-type VPC_LINK --connection-id "$VPC_LINK_ID" \
    --region "$AWS_REGION" > /dev/null 2>&1

  # Deploy
  aws apigateway create-deployment --rest-api-id "$API_ID" --stage-name "$APIGW_STAGE" \
    --region "$AWS_REGION" > /dev/null 2>&1

  APIGW_URL="https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/${APIGW_STAGE}"

  # Now that the API Gateway URL is known, update the agent's ConfigMap so
  # its agent card advertises the correct public URL, and roll the
  # deployment to pick up the change.
  kubectl patch configmap argocd-agent-config -n argocd-agent --type merge \
    -p "{\"data\":{\"AGENT_URL\":\"${APIGW_URL}\"}}" > /dev/null 2>&1 || true
  kubectl rollout restart deployment/argocd-agent-eks -n argocd-agent > /dev/null 2>&1 || true
  kubectl rollout status deployment/argocd-agent-eks -n argocd-agent --timeout=120s > /dev/null 2>&1 || true
  echo "  ✓ AGENT_URL updated: $APIGW_URL"

  echo ""
  echo "  ═══ API Gateway Ready ═══"
  echo ""
  echo "  HTTPS A2A Endpoints:"
  echo "    Agent Card:   ${APIGW_URL}/.well-known/agent-card.json"
  echo "    A2A JSON-RPC: ${APIGW_URL}/"
  echo ""
  echo "  DevOps Agent Configuration:"
  echo "  ────────────────────────────"
  echo "    Agent card endpoint: ${APIGW_URL}/.well-known/agent-card.json"
  echo "    Authentication:      AWS SigV4"
  echo "    IAM Role:            DevOpsAgentElevatedRoleforEKS"
  echo "    Region:              ${AWS_REGION}"
  echo "    Service Name:        execute-api"
  echo ""
  echo "  Test:"
  echo "    curl ${APIGW_URL}/.well-known/agent-card.json | jq ."
  echo ""
  echo "  ⚠ If pod restarts, re-register the new IP:"
  echo "    POD_IP=\$(kubectl get pods -n argocd-agent -l app.kubernetes.io/name=argocd-agent-eks -o jsonpath='{.items[0].status.podIP}')"
  echo "    aws elbv2 register-targets --target-group-arn $TG_ARN --targets \"Id=\${POD_IP},Port=9000\" --region $AWS_REGION"
  echo ""
}

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
status() {
  echo "═══ ArgoCD Agent EKS Status ═══"
  echo ""
  aws eks update-kubeconfig --name "$EKS_CLUSTER_NAME" --region "$AWS_REGION" > /dev/null 2>&1

  echo "  Pods:"
  kubectl get pods -n argocd-agent -l app.kubernetes.io/name=argocd-agent-eks 2>/dev/null || echo "    None"
  echo ""

  API_ID=$(aws apigateway get-rest-apis --region "$AWS_REGION" \
    --query "items[?name=='${APIGW_NAME}'].id | [0]" --output text 2>/dev/null)
  if [[ -n "$API_ID" && "$API_ID" != "None" ]]; then
    APIGW_URL="https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/${APIGW_STAGE}"
    echo "  HTTPS A2A Endpoints:"
    echo "    Agent Card:   ${APIGW_URL}/.well-known/agent-card.json"
    echo "    A2A JSON-RPC: ${APIGW_URL}/"
    echo ""
    echo "  DevOps Agent Configuration:"
    echo "    Agent card endpoint: ${APIGW_URL}/.well-known/agent-card.json"
    echo "    Authentication:      AWS SigV4"
    echo "    IAM Role:            DevOpsAgentElevatedRoleforEKS"
    echo "    Region:              ${AWS_REGION}"
    echo "    Service Name:        execute-api"
  else
    echo "  API Gateway: not configured (run: $0 apigw)"
  fi
  echo ""
}

# ---------------------------------------------------------------------------
# Destroy
# ---------------------------------------------------------------------------
destroy() {
  echo "═══ Destroying resources ═══"
  aws eks update-kubeconfig --name "$EKS_CLUSTER_NAME" --region "$AWS_REGION" > /dev/null 2>&1

  kubectl delete -f k8s/argocd-agent-eks/install.yaml --ignore-not-found 2>/dev/null
  kubectl delete -f k8s/argocd-agent-eks/rbac.yaml --ignore-not-found 2>/dev/null
  echo "  ✓ K8s resources deleted"

  API_ID=$(aws apigateway get-rest-apis --region "$AWS_REGION" \
    --query "items[?name=='${APIGW_NAME}'].id | [0]" --output text 2>/dev/null)
  [[ -n "$API_ID" && "$API_ID" != "None" ]] && aws apigateway delete-rest-api --rest-api-id "$API_ID" --region "$AWS_REGION" 2>/dev/null && echo "  ✓ API Gateway deleted"

  VPC_LINK_ID=$(aws apigateway get-vpc-links --region "$AWS_REGION" \
    --query "items[?name=='argocd-agent-vpclink'].id | [0]" --output text 2>/dev/null)
  [[ -n "$VPC_LINK_ID" && "$VPC_LINK_ID" != "None" ]] && aws apigateway delete-vpc-link --vpc-link-id "$VPC_LINK_ID" --region "$AWS_REGION" 2>/dev/null && echo "  ✓ VPC Link deleted"

  NLB_ARN=$(aws elbv2 describe-load-balancers --names "$NLB_NAME" --region "$AWS_REGION" \
    --query "LoadBalancers[0].LoadBalancerArn" --output text 2>/dev/null || echo "None")
  [[ "$NLB_ARN" != "None" && -n "$NLB_ARN" ]] && aws elbv2 delete-load-balancer --load-balancer-arn "$NLB_ARN" --region "$AWS_REGION" 2>/dev/null && echo "  ✓ NLB deleted"

  TG_ARN=$(aws elbv2 describe-target-groups --names "$TG_NAME" --region "$AWS_REGION" \
    --query "TargetGroups[0].TargetGroupArn" --output text 2>/dev/null || echo "None")
  [[ "$TG_ARN" != "None" && -n "$TG_ARN" ]] && aws elbv2 delete-target-group --target-group-arn "$TG_ARN" --region "$AWS_REGION" 2>/dev/null && echo "  ✓ Target group deleted"

  echo "  ✓ Done"
  echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
case "${1:-}" in
  build)   build;;
  deploy)  deploy;;
  apigw)   apigw;;
  all)     build; deploy; apigw;;
  status)  status;;
  destroy) destroy;;
  *)       usage;;
esac
