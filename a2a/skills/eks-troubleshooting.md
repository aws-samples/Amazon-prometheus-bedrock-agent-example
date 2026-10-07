# EKS Troubleshooting Skill

## Remote Agents

- `argocds-ekd` — EKS troubleshooting and remediation (diagnostics, memory adjustment, restart/rollback)
- `guardrail-agent` — Validates guardrail conditions before allowing destructive changes

Do not use Container Insights, CloudWatch, kubectl, or any other method.

## Retry Policy

If a remote agent times out or is unreachable, retry up to 3 times with a 10-second pause between attempts before giving up.

## Environment Variables

Pass these to the remote agents in your messages:
- EKS_CLUSTER_NAME: Use the cluster name from the investigation trigger
- Region: Use the current AWS region

## Investigation Workflow

### Step 1: Run Diagnostics

Always start by requesting K8sGPT diagnostics from `argocds-ekd`. Send a concise, direct message:

"Run k8sgpt-diagnostics for cluster {EKS_CLUSTER_NAME} in {Region}"

Do NOT include lengthy instructions or environment variable formatting. Keep the prompt short and direct.

### Step 2: Analyze Results

Summarize the diagnostic results:
- Pod issues (OOMKilled, CrashLoopBackOff, ImagePullErrors)
- Deployment problems
- Node health
- Resource constraints

### Step 3: Guardrail Check (MANDATORY before ANY change)

Before making ANY change to the cluster (memory adjustment, restart, rollback, sync, or any other modification), you MUST first check with the `guardrail-agent`:

"Check PDB for deployment {deployment_name} in namespace {namespace}"

**Rules:**
- If the guardrail agent returns `"allowed": true` — proceed with the change FOR THAT SPECIFIC DEPLOYMENT ONLY
- If the guardrail agent returns `"allowed": false` — DO NOT make ANY change that affects that deployment. This includes:
  - Memory adjustments
  - Restarts
  - Rollbacks
  - Syncs
  - Any operation that could disrupt pods in that deployment
- Each deployment requires its own separate guardrail check
- A guardrail approval for one deployment does NOT grant permission to modify other deployments
- If the guardrail agent is unreachable after 3 retries, do NOT proceed — document as blocked

**NEVER bypass guardrails. NEVER perform rollbacks, restarts, or any destructive action on a deployment that was denied by guardrails, even if it shares an ArgoCD application with an approved deployment.**

### Step 4: Remediate OOMKilled Pods (ONLY if guardrail allows for THAT specific deployment)

If diagnostics reveal a pod with OOMKilled reason AND the guardrail check returns `"allowed": true` for that deployment:

1. Determine the current memory limit from the diagnostic details
2. Double the memory limit (e.g., 100Mi → 200Mi, 200Mi → 400Mi, 512Mi → 1Gi)
3. Send the adjustment to `argocds-ekd`:
   "Adjust memory for pod {pod_name} in app {app_name} to {doubled_limit}"
4. Wait 90 seconds for the pod to restart and K8sGPT to update its results
5. Check ArgoCD application status:
   "Check status of app {app_name} in cluster {EKS_CLUSTER_NAME}"
6. Then run diagnostics again:
   "Run k8sgpt-diagnostics for cluster {EKS_CLUSTER_NAME} in {Region}"
7. If the pod is STILL OOMKilled, double the limit again and repeat from step 3
8. If the pod is healthy (no longer in diagnostics results), remediation is complete

Maximum 3 doubling attempts. If still OOMKilled after 3 doublings, document as unresolved and recommend manual investigation.

**If the ArgoCD sync fails after a memory adjustment, DO NOT perform a rollback unless the guardrail agent has approved ALL deployments in that application. A rollback affects the entire application and all its deployments.**

### Step 5: Report

Provide investigation results as a summary including:
- Initial diagnostic findings
- Guardrail check results (allowed/denied for each deployment)
- Actions taken (each memory adjustment with before/after values)
- Actions BLOCKED by guardrails (list each denied deployment and reason)
- Final pod status (resolved or unresolved)
- Remaining issues (if any)

## Important Notes

- Keep prompts to remote agents SHORT and DIRECT (under 50 words)
- Remote agents have their own LLM — do not over-instruct them
- Only use A2A protocol to communicate with remote agents
- If memory adjustment is needed, specify: app_name, pod_name, and new memory_limit
- Always verify after remediation by running diagnostics again
- ALWAYS check guardrails before making ANY destructive change
- Guardrail approval is PER DEPLOYMENT — not per application
- A rollback is a destructive action that affects ALL deployments in an app
- If ANY deployment in an app is denied by guardrails, do NOT rollback that app
- If guardrails deny the change, respect the decision and document it — do not attempt workarounds
