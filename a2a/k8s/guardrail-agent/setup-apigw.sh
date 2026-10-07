#!/usr/bin/env bash
set -eo pipefail
cd "$(dirname "$0")/../.."

# AWS_REGION and EKS_CLUSTER (cluster name) are read from the environment;
# they fall back to the workshop defaults only if unset.
AWS_REGION="${AWS_REGION:-us-east-1}"
EKS_CLUSTER_NAME="${EKS_CLUSTER:-ws-eks-1}"
APIGW_NAME="argocd-eks-agent-api"
NLB_NAME="${NLB_NAME:-argocd-agent-nlb}"

VPC_ID=$(aws eks describe-cluster --name "$EKS_CLUSTER_NAME" --region "$AWS_REGION" \
  --query "cluster.resourcesVpcConfig.vpcId" --output text)

# This script adds the /guardrail route to the ArgoCD agent's existing API
# Gateway, so it reuses that API's ID (looked up by name) unless API_ID is
# explicitly provided via the environment.
API_ID="${API_ID:-$(aws apigateway get-rest-apis --region "$AWS_REGION" \
  --query "items[?name=='${APIGW_NAME}'].id | [0]" --output text)}"
if [[ -z "$API_ID" || "$API_ID" == "None" ]]; then
  echo "Error: Could not find API Gateway '${APIGW_NAME}'. Run deploy-argocd-eks.sh apigw first, or set API_ID." >&2
  exit 1
fi

# Node security group used by the shared NLB's targets (resolved dynamically
# unless overridden via the NODE_SG env var)
NODE_SG="${NODE_SG:-$(aws ec2 describe-security-groups --region "$AWS_REGION" \
  --filters "Name=tag:aws:eks:cluster-name,Values=${EKS_CLUSTER_NAME}" \
  --query "SecurityGroups[0].GroupId" --output text)}"

echo "═══ Adding Guardrail Agent to API Gateway ═══"

# Step 1: Create target group for guardrail agent (port 9001)
echo "Step 1: Target group..."
TG_ARN=$(aws elbv2 describe-target-groups --names guardrail-agent-tg --region "$AWS_REGION" \
  --query "TargetGroups[0].TargetGroupArn" --output text 2>/dev/null || echo "None")

if [[ "$TG_ARN" == "None" || -z "$TG_ARN" ]]; then
  TG_ARN=$(aws elbv2 create-target-group \
    --name guardrail-agent-tg --protocol TCP --port 9001 \
    --vpc-id "$VPC_ID" --target-type ip \
    --health-check-protocol HTTP --health-check-path /.well-known/agent-card.json --health-check-port 9001 \
    --region "$AWS_REGION" --query "TargetGroups[0].TargetGroupArn" --output text)
  echo "  ✓ Created"
else
  echo "  ✓ Exists"
fi

# Register pod IP
POD_IP=$(kubectl get pods -n guardrail-agent -l app.kubernetes.io/name=guardrail-agent -o jsonpath='{.items[0].status.podIP}')
aws elbv2 register-targets --target-group-arn "$TG_ARN" --targets "Id=${POD_IP},Port=9001" --region "$AWS_REGION"
echo "  ✓ Pod registered: $POD_IP"

# Step 2: Add listener on NLB for port 9001
echo "Step 2: NLB listener..."
NLB_ARN=$(aws elbv2 describe-load-balancers --names "$NLB_NAME" --region "$AWS_REGION" \
  --query "LoadBalancers[0].LoadBalancerArn" --output text)

LISTENER_EXISTS=$(aws elbv2 describe-listeners --load-balancer-arn "$NLB_ARN" --region "$AWS_REGION" \
  --query "Listeners[?Port==\`9001\`].ListenerArn" --output text 2>/dev/null)

if [[ -z "$LISTENER_EXISTS" ]]; then
  aws elbv2 create-listener \
    --load-balancer-arn "$NLB_ARN" --protocol TCP --port 9001 \
    --default-actions "Type=forward,TargetGroupArn=${TG_ARN}" \
    --region "$AWS_REGION" > /dev/null
  echo "  ✓ Listener created (port 9001)"
else
  echo "  ✓ Listener exists"
fi

NLB_DNS=$(aws elbv2 describe-load-balancers --load-balancer-arns "$NLB_ARN" --region "$AWS_REGION" \
  --query "LoadBalancers[0].DNSName" --output text)

# Enable cross-zone load balancing
aws elbv2 modify-load-balancer-attributes \
  --load-balancer-arn "$NLB_ARN" \
  --attributes Key=load_balancing.cross_zone.enabled,Value=true \
  --region "$AWS_REGION" > /dev/null
echo "  ✓ Cross-zone load balancing enabled"

# Step 3: Get VPC Link ID
VPC_LINK_ID=$(aws apigateway get-vpc-links --region "$AWS_REGION" \
  --query "items[?name=='argocd-agent-vpclink'].id | [0]" --output text)
echo "  VPC Link: $VPC_LINK_ID"

# Step 4: Add /guardrail path to API Gateway
echo "Step 3: API Gateway resources..."
ROOT_ID=$(aws apigateway get-resources --rest-api-id "$API_ID" --region "$AWS_REGION" \
  --query "items[?path=='/'].id" --output text)

GUARDRAIL_ID=$(aws apigateway get-resources --rest-api-id "$API_ID" --region "$AWS_REGION" \
  --query "items[?pathPart=='guardrail'].id" --output text 2>/dev/null)
if [[ -z "$GUARDRAIL_ID" || "$GUARDRAIL_ID" == "None" ]]; then
  GUARDRAIL_ID=$(aws apigateway create-resource --rest-api-id "$API_ID" \
    --parent-id "$ROOT_ID" --path-part "guardrail" \
    --region "$AWS_REGION" --query "id" --output text)
fi

GUARDRAIL_PROXY_ID=$(aws apigateway get-resources --rest-api-id "$API_ID" --region "$AWS_REGION" \
  --query "items[?path=='/guardrail/{proxy+}'].id" --output text 2>/dev/null)
if [[ -z "$GUARDRAIL_PROXY_ID" || "$GUARDRAIL_PROXY_ID" == "None" ]]; then
  GUARDRAIL_PROXY_ID=$(aws apigateway create-resource --rest-api-id "$API_ID" \
    --parent-id "$GUARDRAIL_ID" --path-part "{proxy+}" \
    --region "$AWS_REGION" --query "id" --output text)
fi

# ANY on /guardrail
aws apigateway put-method --rest-api-id "$API_ID" --resource-id "$GUARDRAIL_ID" \
  --http-method ANY --authorization-type NONE \
  --region "$AWS_REGION" > /dev/null 2>&1 || true

aws apigateway put-integration --rest-api-id "$API_ID" --resource-id "$GUARDRAIL_ID" \
  --http-method ANY --type HTTP_PROXY --integration-http-method ANY \
  --uri "http://${NLB_DNS}:9001/" --connection-type VPC_LINK --connection-id "$VPC_LINK_ID" \
  --region "$AWS_REGION" > /dev/null

# ANY on /guardrail/{proxy+}
aws apigateway put-method --rest-api-id "$API_ID" --resource-id "$GUARDRAIL_PROXY_ID" \
  --http-method ANY --authorization-type NONE \
  --request-parameters "method.request.path.proxy=true" \
  --region "$AWS_REGION" > /dev/null 2>&1 || true

aws apigateway put-integration --rest-api-id "$API_ID" --resource-id "$GUARDRAIL_PROXY_ID" \
  --http-method ANY --type HTTP_PROXY --integration-http-method ANY \
  --uri "http://${NLB_DNS}:9001/{proxy}" --connection-type VPC_LINK --connection-id "$VPC_LINK_ID" \
  --request-parameters "integration.request.path.proxy=method.request.path.proxy" \
  --region "$AWS_REGION" > /dev/null

# Redeploy
aws apigateway create-deployment --rest-api-id "$API_ID" --stage-name prod --region "$AWS_REGION" > /dev/null
echo "  ✓ API Gateway deployed"

# Allow port 9001 from VPC CIDR on node SG
VPC_CIDR=$(aws ec2 describe-vpcs --vpc-ids "$VPC_ID" --region "$AWS_REGION" --query "Vpcs[0].CidrBlock" --output text)
aws ec2 authorize-security-group-ingress --group-id "$NODE_SG" \
  --protocol tcp --port 9001 --cidr "$VPC_CIDR" --region "$AWS_REGION" 2>/dev/null || true

GUARDRAIL_URL="https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/prod/guardrail"

# Now that the route exists, update the guardrail agent's ConfigMap so its
# agent card advertises the correct public URL, and roll the deployment.
kubectl patch configmap guardrail-agent-config -n guardrail-agent --type merge \
  -p "{\"data\":{\"AGENT_URL\":\"${GUARDRAIL_URL}\"}}" > /dev/null 2>&1 || true
kubectl rollout restart deployment/guardrail-agent -n guardrail-agent > /dev/null 2>&1 || true
kubectl rollout status deployment/guardrail-agent -n guardrail-agent --timeout=120s > /dev/null 2>&1 || true
echo "  ✓ AGENT_URL updated: $GUARDRAIL_URL"

echo ""
echo "═══ Done ═══"
echo ""
echo "  Guardrail Agent Endpoints:"
echo "    Agent Card: ${GUARDRAIL_URL}/.well-known/agent-card.json"
echo "    A2A:        ${GUARDRAIL_URL}/"
echo ""
echo "  DevOps Agent Configuration:"
echo "    Agent card endpoint: ${GUARDRAIL_URL}/.well-known/agent-card.json"
echo "    Authentication: AWS SigV4"
echo "    IAM Role: DevOpsAgentElevatedRoleforEKS"
echo "    Region: ${AWS_REGION}"
echo "    Service Name: execute-api"
echo ""
