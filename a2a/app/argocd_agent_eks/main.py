"""ArgoCD Agent — A2A server for EKS deployment.

Runs as a standalone A2A-compatible server on port 9000 using FastAPI.
Implements the A2A JSON-RPC protocol (message/send) and serves agent card.
Deployed directly on EKS with in-cluster Kubernetes access.
"""

import asyncio
import sys
import os
import logging
import re
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from strands import Agent

from app.argocd_agent_eks.tools.restart_rollback import restart_rollback_argocd
from app.argocd_agent_eks.tools.memory_adjustment import adjust_memory
from app.argocd_agent_eks.tools.k8sgpt_diagnostics import get_k8sgpt_diagnostics
from app.argocd_agent_eks.config import get_config

SYSTEM_PROMPT = """You are an ArgoCD and Kubernetes operations assistant.
You help platform engineers manage their ArgoCD applications and diagnose Kubernetes cluster issues.

Available operations:
1. **Restart/Rollback** - Restart (sync) or rollback an ArgoCD application
2. **Memory Adjustment** - Adjust memory limits and requests for Helm-managed applications
3. **K8sGPT Diagnostics** - Retrieve diagnostic results from K8sGPT running on an EKS cluster

When a user requests an operation, extract the required parameters from their message
and invoke the appropriate tool. Report results clearly and concisely.

The default EKS cluster is 'ws-eks-1' in us-east-1 unless the user specifies otherwise."""

AGENT_CARD = {
    "name": "ArgoCD Operations Agent",
    "description": (
        "ArgoCD and Kubernetes operations agent that can restart/rollback "
        "applications, adjust memory resources for Helm-managed apps, and retrieve "
        "K8sGPT diagnostic results from Amazon EKS clusters."
    ),
    "url": os.environ.get("AGENT_URL", "http://localhost:9000"),
    "version": "1.0.0",
    "protocolVersion": "0.3.0",
    "preferredTransport": "JSONRPC",
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text"],
    "capabilities": {"streaming": False, "pushNotifications": False},
    "supportedInterfaces": [
        {
            "url": os.environ.get("AGENT_URL", "http://localhost:9000"),
            "protocol": "JSONRPC",
            "protocolVersion": "0.3.0"
        }
    ],
    "skills": [
        {
            "id": "restart-rollback",
            "name": "Restart or Rollback ArgoCD Application",
            "description": "Restart (sync) or rollback an ArgoCD application.",
            "tags": ["argocd", "restart", "rollback", "sync"],
            "examples": ["Restart the frontend application", "Rollback payments-service"],
        },
        {
            "id": "memory-adjustment",
            "name": "Adjust Application Memory",
            "description": "Adjust memory limits and requests for a Helm-managed ArgoCD application.",
            "tags": ["argocd", "helm", "memory", "resources"],
            "examples": ["Set memory limit to 1Gi for api-gateway in backend app"],
        },
        {
            "id": "k8sgpt-diagnostics",
            "name": "K8sGPT Cluster Diagnostics",
            "description": "Retrieve K8sGPT diagnostic results from an EKS cluster.",
            "tags": ["kubernetes", "eks", "diagnostics", "k8sgpt"],
            "examples": ["Get diagnostics for ws-eks-1", "Check cluster health"],
        },
    ],
}

app = FastAPI(title="ArgoCD Agent A2A")

_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = Agent(
            system_prompt=SYSTEM_PROMPT,
            tools=[restart_rollback_argocd, adjust_memory, get_k8sgpt_diagnostics],
        )
    return _agent


@app.get("/.well-known/agent-card.json")
async def agent_card(request: Request):
    card = dict(AGENT_CARD)
    # Use AGENT_URL env var (set to API Gateway URL) or derive from request
    agent_url = os.environ.get("AGENT_URL", "")
    if not agent_url or agent_url == "http://localhost:9000":
        # Try to use x-forwarded headers from API Gateway
        proto = request.headers.get("x-forwarded-proto", "https")
        host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost:9000"))
        agent_url = f"{proto}://{host}"
    card["url"] = agent_url
    card["supportedInterfaces"] = [
        {
            "url": agent_url,
            "protocol": "JSONRPC",
            "protocolVersion": "0.3.0"
        }
    ]
    return JSONResponse(content=card)


@app.get("/ping")
async def ping():
    return {"status": "ok"}


@app.post("/")
async def a2a_jsonrpc(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})

    jsonrpc_id = body.get("id")
    method = body.get("method")
    params = body.get("params", {})

    if method == "message/send":
        return await handle_message_send(jsonrpc_id, params)
    return JSONResponse(content={"jsonrpc": "2.0", "id": jsonrpc_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})


# --- Fast-path parsing helpers (avoid the LLM tool-use loop for known ops) ---
# Kubernetes resource names are RFC 1123 labels (lowercase alnum + '-'); memory
# quantities use suffixes like Mi/Gi. Parsing the structured message the DevOps
# Agent skill sends ("Adjust memory for pod {pod} in app {app} to {limit}")
# lets us skip the Bedrock round-trips that push the request past API Gateway's
# 29s integration timeout.
_MEM_RE = re.compile(r"\b\d+(?:\.\d+)?(?:Mi|Gi|Ti|Ki|M|G|T|K)\b")
_POD_RE = re.compile(r"\bpod\s+([A-Za-z0-9][A-Za-z0-9-]*)", re.IGNORECASE)
_APP_RE = re.compile(r"\bapp(?:lication)?\s+([A-Za-z0-9][A-Za-z0-9-]*)", re.IGNORECASE)


def _parse_memory_adjustment(text: str):
    """Extract (app_name, pod_name, memory_limit) from a memory-adjust request.

    Returns None if any component is missing, in which case the caller should
    fall back to the LLM to interpret the request.
    """
    pod_m = _POD_RE.search(text)
    app_m = _APP_RE.search(text)
    mem_m = _MEM_RE.search(text)
    if pod_m and app_m and mem_m:
        return app_m.group(1), pod_m.group(1), mem_m.group(0)
    return None


def _extract_agent_text(result) -> str:
    """Safely extract the text content from a Strands Agent result."""
    if hasattr(result, "message"):
        msg = result.message
        if isinstance(msg, str):
            return msg
        if isinstance(msg, dict):
            return msg.get("content", [{}])[0].get("text", str(msg)) if "content" in msg else str(msg)
        return str(msg)
    return str(result)


def _process_request(user_input: str) -> str:
    """Run the (blocking) LLM + tool processing for a user request.

    This function is intentionally synchronous and MUST be executed off the
    asyncio event loop (via asyncio.to_thread). It performs blocking work —
    Bedrock LLM inference, time.sleep pauses, and Kubernetes API calls — that
    would otherwise stall the single uvicorn event loop, preventing it from
    answering NLB health checks and concurrent requests. When the loop stalls,
    the NLB/API Gateway connection is reset and surfaces as a 500 "internal
    error" from API Gateway.
    """
    user_lower = user_input.lower()

    if any(kw in user_lower for kw in ["k8sgpt", "diagnostics", "diagnostic", "cluster health", "check health"]):
        # Fast-path: direct diagnostics call (no LLM)
        config = get_config()
        cluster = config.eks_cluster_name
        for word in user_input.split():
            if word.startswith("ws-") or word.startswith("eks-"):
                cluster = word.rstrip(".,;:")
                break
        result = get_k8sgpt_diagnostics(eks_cluster=cluster)
        return f"K8sGPT Diagnostics for cluster {cluster}:\n{result}"

    if "memory" in user_lower and ("adjust" in user_lower or "increase" in user_lower or "set" in user_lower or "limit" in user_lower):
        # Fast-path: parse params directly and call the tool, skipping the LLM
        # tool-use loop. Fall back to the LLM only if parsing fails.
        parsed = _parse_memory_adjustment(user_input)
        if parsed:
            app_name, pod_name, memory_limit = parsed
            logger.info(
                "Memory adjustment fast-path: app=%s pod=%s limit=%s",
                app_name, pod_name, memory_limit,
            )
            return adjust_memory(app_name=app_name, pod_name=pod_name, memory_limit=memory_limit)
        logger.info("Memory adjustment: params not parseable, using LLM")
        return _extract_agent_text(get_agent()(user_input))

    if "restart" in user_lower or "rollback" in user_lower or "sync" in user_lower:
        # Restart/rollback — delegate to LLM to extract params
        return _extract_agent_text(get_agent()(user_input))

    # Default: use full agent with LLM
    return _extract_agent_text(get_agent()(user_input))


async def handle_message_send(jsonrpc_id, params: dict):
    message = params.get("message", {})
    parts = message.get("parts", [])
    text_parts = [p.get("text", "") for p in parts if p.get("kind") == "text"]
    user_input = " ".join(text_parts).strip()

    if not user_input:
        return JSONResponse(content={"jsonrpc": "2.0", "id": jsonrpc_id, "error": {"code": -32602, "message": "No text in message"}})

    try:
        # Offload blocking work (LLM inference, sleeps, K8s calls) to a worker
        # thread so the event loop stays responsive to health checks and
        # concurrent requests while the operation runs.
        output = await asyncio.to_thread(_process_request, user_input)

        task_id = str(uuid.uuid4())
        context_id = str(uuid.uuid4())
        return JSONResponse(content={
            "jsonrpc": "2.0",
            "id": jsonrpc_id,
            "result": {
                "kind": "task",
                "id": task_id,
                "contextId": context_id,
                "status": {"state": "completed"},
                "artifacts": [
                    {
                        "artifactId": str(uuid.uuid4()),
                        "parts": [{"kind": "text", "text": output}]
                    }
                ],
            },
        })
    except Exception as e:
        logger.exception("Error processing message")
        return JSONResponse(content={"jsonrpc": "2.0", "id": jsonrpc_id, "error": {"code": -32603, "message": f"Internal error: {type(e).__name__}"}})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "9000"))
    uvicorn.run("app.argocd_agent_eks.main:app", host="0.0.0.0", port=port, log_level="info")
