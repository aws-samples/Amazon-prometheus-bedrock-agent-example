"""Guardrail Agent — A2A server for EKS deployment.

Validates guardrail conditions before allowing destructive changes
to deployments on EKS via ArgoCD. Currently checks:
- Pod Disruption Budget (PDB) exists for target deployments

Runs on port 9001 as a FastAPI A2A server.
"""

import sys
import os
import logging
import uuid
import json

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.guardrail_agent.tools.pdb_check import check_pdb
from app.guardrail_agent.config import get_config

AGENT_CARD = {
    "name": "Guardrail Agent",
    "description": (
        "Validates guardrail conditions before allowing destructive changes "
        "to EKS deployments. Checks Pod Disruption Budgets and other safety "
        "requirements before permitting restarts, rollbacks, or scaling operations."
    ),
    "url": os.environ.get("AGENT_URL", "http://localhost:9001"),
    "version": "1.0.0",
    "protocolVersion": "0.3.0",
    "preferredTransport": "JSONRPC",
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text"],
    "capabilities": {"streaming": False, "pushNotifications": False},
    "supportedInterfaces": [
        {
            "url": os.environ.get("AGENT_URL", "http://localhost:9001"),
            "protocol": "JSONRPC",
            "protocolVersion": "0.3.0",
        }
    ],
    "skills": [
        {
            "id": "pdb-check",
            "name": "Pod Disruption Budget Check",
            "description": (
                "Check if a Pod Disruption Budget exists for a deployment before "
                "allowing destructive changes. Returns allowed/denied with reason."
            ),
            "tags": ["guardrail", "pdb", "safety", "availability", "disruption"],
            "examples": [
                "Check PDB for deployment nginx in namespace default",
                "Is it safe to restart deployment frontend in production?",
                "Validate guardrails for deployment helm-guestbook in helm-guestbook namespace",
            ],
        },
    ],
}

app = FastAPI(title="Guardrail Agent")


@app.get("/.well-known/agent-card.json")
async def agent_card(request: Request):
    card = dict(AGENT_CARD)
    agent_url = os.environ.get("AGENT_URL", "")
    if not agent_url or agent_url == "http://localhost:9001":
        proto = request.headers.get("x-forwarded-proto", "http")
        host = request.headers.get("host", "localhost:9001")
        agent_url = f"{proto}://{host}"
    card["url"] = agent_url
    card["supportedInterfaces"] = [
        {"url": agent_url, "protocol": "JSONRPC", "protocolVersion": "0.3.0"}
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


async def handle_message_send(jsonrpc_id, params: dict):
    message = params.get("message", {})
    parts = message.get("parts", [])
    text_parts = [p.get("text", "") for p in parts if p.get("kind") == "text"]
    user_input = " ".join(text_parts).strip()

    if not user_input:
        return JSONResponse(content={"jsonrpc": "2.0", "id": jsonrpc_id, "error": {"code": -32602, "message": "No text in message"}})

    try:
        user_lower = user_input.lower()

        # Extract deployment name and namespace from input
        deployment_name = ""
        namespace = "default"

        # Parse common patterns
        words = user_input.split()
        for i, word in enumerate(words):
            if word.lower() in ("deployment", "deploy") and i + 1 < len(words):
                deployment_name = words[i + 1].strip(".,;:'\"")
            if word.lower() in ("namespace", "ns") and i + 1 < len(words):
                namespace = words[i + 1].strip(".,;:'\"")
            if word.lower() == "in" and i + 1 < len(words):
                namespace = words[i + 1].strip(".,;:'\"")

        # If no deployment found, try to find any likely name
        if not deployment_name:
            for word in words:
                if word not in ("check", "pdb", "for", "deployment", "in", "namespace",
                               "is", "it", "safe", "to", "restart", "validate",
                               "guardrails", "guardrail", "the", "a", "pod", "disruption",
                               "budget", "allowed", "ns", "default"):
                    deployment_name = word.strip(".,;:'\"")
                    break

        if not deployment_name:
            output = json.dumps({
                "allowed": False,
                "reason": "Could not determine deployment name from request. Please specify: 'Check PDB for deployment <name> in namespace <ns>'",
                "pdb_details": None,
            })
        else:
            output = check_pdb(deployment_name=deployment_name, namespace=namespace)

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
                        "parts": [{"kind": "text", "text": output}],
                    }
                ],
            },
        })
    except Exception as e:
        logger.exception("Error processing message")
        return JSONResponse(content={"jsonrpc": "2.0", "id": jsonrpc_id, "error": {"code": -32603, "message": f"Internal error: {type(e).__name__}"}})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "9001"))
    uvicorn.run("app.guardrail_agent.main:app", host="0.0.0.0", port=port, log_level="info")
