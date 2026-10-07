#!/usr/bin/env bash
set -eo pipefail

# ===========================================================================
# Setup API Gateway + VPC Link for ArgoCD Agent on EKS
# Provides a trusted HTTPS endpoint for the DevOps agent
#
# Architecture:
#   DevOps Agent → API Gateway (HTTPS) → VPC Link → NLB → Pod (port 9000)
#
# Prerequisites:
#   - ArgoCD agent running on EKS (deploy-argocd-eks.sh completed)
#   - VPC BPA exclusion in place for the EKS VPC
# ===========================================================================

# AWS_REGION and EKS_CLUSTER (cluster name) are read from the environment;
# they fall back to the workshop defaults only if unset.
AWS_REGION="${AWS_REGION:-us-east-1}"
EKS_CLUSTER_NAME="${EKS_CLUSTER:-ws-eks-1}"
APIGW_NAME="argocd-eks-agent-api"
APIGW_STAGE="prod"
NLB_NAME="argocd-agent-nlb"
TG_NAME="argocd-agent-tg"

echo "═══ Setting up API Gateway for ArgoCD Agent ═══"
echo ""

# --- Step 1: Get VPC and subnet info ---
echo "Step 1: Getting cluster networking info..."
VPC_ID=$(aws eks describe-cluster --name "$EKS_CLUSTER_NAME" --region "$AWS_REGION" \
  --query "cluster.resourcesVpcConfig.vpcId" --output text)
SUBNETS=$(aws ec2 describe-subnets \
  --subnet-ids $(aws eks describe-cluster --name "$EKS_CLUSTER_NAME" --region "$AWS_REGION" \
    --query "cluster.resourcesVpcConfig.subnetIds[]" --output text) \
  --region "$AWS_REGION" \
  --query "Subnets[].[AvailabilityZone,SubnetId]" --output text | sort -k1,1 -u | awk '{print $2}')
echo "  VPC: $VPC_ID"
echo "  Subnets (one per AZ): $SUBNETS"
echo ""

# --- Step 2: Get pod IP ---
echo "Step 2: Getting agent pod IP..."
POD_IP=$(kubectl get pods -n argocd-agent -l app.kubernetes.io/name=argocd-agent-eks \
  -o jsonpath='{.items[0].status.podIP}')
echo "  Pod IP: $POD_IP"
echo ""

# --- Step 3: Create Target Group ---
echo "Step 3: Creating target group..."
TG_ARN=$(aws elbv2 describe-target-groups --names "$TG_NAME" --region "$AWS_REGION" \
  --query "TargetGroups[0].TargetGroupArn" --output text 2>/dev/null || echo "None")

if [[ "$TG_ARN" == "None" || -z "$TG_ARN" ]]; then
  TG_ARN=$(aws elbv2 create-target-group \
    --name "$TG_NAME" \
    --protocol TCP \
    --port 9000 \
    --vpc-id "$VPC_ID" \
    --target-type ip \
    --health-check-protocol HTTP \
    --health-check-path /.well-known/agent-card.json \
    --health-check-port 9000 \
    --region "$AWS_REGION" \
    --query "TargetGroups[0].TargetGroupArn" --output text)
  echo "  ✓ Target group created: $TG_ARN"
else
  echo "  ✓ Target group exists: $TG_ARN"
fi

# Register pod IP
aws elbv2 register-targets \
  --target-group-arn "$TG_ARN" \
  --targets "Id=${POD_IP},Port=9000" \
  --region "$AWS_REGION" 2>/dev/null
echo "  ✓ Pod IP registered: $POD_IP"
echo ""

# --- Step 4: Create internal NLB ---
echo "Step 4: Creating internal NLB..."
NLB_ARN=$(aws elbv2 describe-load-balancers --names "$NLB_NAME" --region "$AWS_REGION" \
  --query "LoadBalancers[0].LoadBalancerArn" --output text 2>/dev/null || echo "None")

if [[ "$NLB_ARN" == "None" || -z "$NLB_ARN" ]]; then
  NLB_ARN=$(aws elbv2 create-load-balancer \
    --name "$NLB_NAME" \
    --type network \
    --scheme internal \
    --subnets $SUBNETS \
    --region "$AWS_REGION" \
    --query "LoadBalancers[0].LoadBalancerArn" --output text)
  echo "  ✓ NLB created: $NLB_ARN"

  # Create listener
  aws elbv2 create-listener \
    --load-balancer-arn "$NLB_ARN" \
    --protocol TCP \
    --port 9000 \
    --default-actions "Type=forward,TargetGroupArn=${TG_ARN}" \
    --region "$AWS_REGION" > /dev/null
  echo "  ✓ Listener created (TCP:9000)"
else
  echo "  ✓ NLB exists: $NLB_ARN"
fi

# Wait for NLB to be active
echo "  Waiting for NLB to become active..."
aws elbv2 wait load-balancer-available --load-balancer-arns "$NLB_ARN" --region "$AWS_REGION"
echo "  ✓ NLB active"

NLB_DNS=$(aws elbv2 describe-load-balancers --load-balancer-arns "$NLB_ARN" --region "$AWS_REGION" \
  --query "LoadBalancers[0].DNSName" --output text)
echo "  NLB DNS: $NLB_DNS"
echo ""

# --- Step 5: Create VPC Link ---
echo "Step 5: Creating VPC Link..."
VPC_LINK_ID=$(aws apigateway get-vpc-links --region "$AWS_REGION" \
  --query "items[?name=='argocd-agent-vpclink'].id | [0]" --output text 2>/dev/null)

if [[ -z "$VPC_LINK_ID" || "$VPC_LINK_ID" == "None" ]]; then
  VPC_LINK_ID=$(aws apigateway create-vpc-link \
    --name argocd-agent-vpclink \
    --target-arns "$NLB_ARN" \
    --region "$AWS_REGION" \
    --query "id" --output text)
  echo "  ✓ VPC Link created: $VPC_LINK_ID"
  echo "  Waiting for VPC Link to become AVAILABLE (3-5 min)..."

  while true; do
    STATUS=$(aws apigateway get-vpc-link --vpc-link-id "$VPC_LINK_ID" --region "$AWS_REGION" \
      --query "status" --output text)
    if [[ "$STATUS" == "AVAILABLE" ]]; then
      break
    fi
    echo "    Status: $STATUS"
    sleep 15
  done
  echo "  ✓ VPC Link AVAILABLE"
else
  echo "  ✓ VPC Link exists: $VPC_LINK_ID"
fi
echo ""

# --- Step 6: Create API Gateway ---
echo "Step 6: Creating API Gateway..."
API_ID=$(aws apigateway get-rest-apis --region "$AWS_REGION" \
  --query "items[?name=='${APIGW_NAME}'].id | [0]" --output text 2>/dev/null)

if [[ -z "$API_ID" || "$API_ID" == "None" ]]; then
  API_ID=$(aws apigateway create-rest-api \
    --name "$APIGW_NAME" \
    --description "HTTPS proxy for ArgoCD Agent on EKS (via VPC Link)" \
    --endpoint-configuration types=REGIONAL \
    --region "$AWS_REGION" \
    --query "id" --output text)
  echo "  ✓ API created: $API_ID"
else
  echo "  ✓ API exists: $API_ID"
fi

# Get root resource
ROOT_ID=$(aws apigateway get-resources --rest-api-id "$API_ID" --region "$AWS_REGION" \
  --query "items[?path=='/'].id" --output text)

# Create {proxy+} resource
PROXY_ID=$(aws apigateway get-resources --rest-api-id "$API_ID" --region "$AWS_REGION" \
  --query "items[?pathPart=='{proxy+}'].id" --output text 2>/dev/null)

if [[ -z "$PROXY_ID" || "$PROXY_ID" == "None" ]]; then
  PROXY_ID=$(aws apigateway create-resource \
    --rest-api-id "$API_ID" \
    --parent-id "$ROOT_ID" \
    --path-part "{proxy+}" \
    --region "$AWS_REGION" \
    --query "id" --output text)
fi

# Setup ANY on /{proxy+} with VPC Link integration
echo "  Configuring proxy integration via VPC Link..."
aws apigateway put-method \
  --rest-api-id "$API_ID" \
  --resource-id "$PROXY_ID" \
  --http-method ANY \
  --authorization-type NONE \
  --request-parameters "method.request.path.proxy=true" \
  --region "$AWS_REGION" > /dev/null 2>&1 || true

aws apigateway put-integration \
  --rest-api-id "$API_ID" \
  --resource-id "$PROXY_ID" \
  --http-method ANY \
  --type HTTP_PROXY \
  --integration-http-method ANY \
  --uri "http://${NLB_DNS}:9000/{proxy}" \
  --connection-type VPC_LINK \
  --connection-id "$VPC_LINK_ID" \
  --request-parameters "integration.request.path.proxy=method.request.path.proxy" \
  --region "$AWS_REGION" > /dev/null 2>&1

# Setup ANY on / (root) for A2A JSON-RPC
aws apigateway put-method \
  --rest-api-id "$API_ID" \
  --resource-id "$ROOT_ID" \
  --http-method ANY \
  --authorization-type NONE \
  --region "$AWS_REGION" > /dev/null 2>&1 || true

aws apigateway put-integration \
  --rest-api-id "$API_ID" \
  --resource-id "$ROOT_ID" \
  --http-method ANY \
  --type HTTP_PROXY \
  --integration-http-method ANY \
  --uri "http://${NLB_DNS}:9000/" \
  --connection-type VPC_LINK \
  --connection-id "$VPC_LINK_ID" \
  --region "$AWS_REGION" > /dev/null 2>&1

# Deploy
echo "  Deploying to stage: $APIGW_STAGE..."
aws apigateway create-deployment \
  --rest-api-id "$API_ID" \
  --stage-name "$APIGW_STAGE" \
  --region "$AWS_REGION" > /dev/null 2>&1

APIGW_URL="https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/${APIGW_STAGE}"
echo ""

# --- Done ---
echo "═══ Setup Complete ═══"
echo ""
echo "  HTTPS A2A Endpoints:"
echo "  ─────────────────────"
echo "    Agent Card:   ${APIGW_URL}/.well-known/agent-card.json"
echo "    A2A JSON-RPC: ${APIGW_URL}/"
echo ""
echo "  DevOps Agent Configuration:"
echo "  ────────────────────────────"
echo "    Agent card endpoint: ${APIGW_URL}/.well-known/agent-card.json"
echo "    Authentication: AWS SigV4"
echo "    IAM Role: DevOpsAgentElevatedRoleforEKS"
echo "    Region: ${AWS_REGION}"
echo "    Service Name: execute-api"
echo ""
echo "  Test commands:"
echo "    curl ${APIGW_URL}/.well-known/agent-card.json | jq ."
echo ""
echo "    curl -X POST ${APIGW_URL}/ \\"
echo "      -H 'Content-Type: application/json' \\"
echo "      -d '{\"jsonrpc\":\"2.0\",\"id\":\"1\",\"method\":\"message/send\",\"params\":{\"message\":{\"role\":\"user\",\"parts\":[{\"kind\":\"text\",\"text\":\"Get diagnostics for ${EKS_CLUSTER_NAME}\"}],\"messageId\":\"msg-001\"}}}'"
echo ""
echo "  ⚠ Note: If pod restarts, re-register the new pod IP:"
echo "    POD_IP=\$(kubectl get pods -n argocd-agent -l app.kubernetes.io/name=argocd-agent-eks -o jsonpath='{.items[0].status.podIP}')"
echo "    aws elbv2 register-targets --target-group-arn $TG_ARN --targets \"Id=\${POD_IP},Port=9000\" --region $AWS_REGION"
echo ""
