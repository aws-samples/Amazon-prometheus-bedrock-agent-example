from bedrock_agentcore.runtime import BedrockAgentCoreApp
from agent import create_agent

app = BedrockAgentCoreApp()
agent = create_agent()


@app.entrypoint
def invoke(payload: dict) -> dict:
    """Process incoming user message and return agent response."""
    try:
        user_message = payload.get("prompt", "")
        if not user_message:
            return {"error": "No prompt provided in payload"}
        result = agent(user_message)
        return {"result": result.message}
    except Exception as e:
        # Return sanitized error without stack traces
        return {"error": f"Request processing failed: {type(e).__name__}"}


if __name__ == "__main__":
    app.run()
