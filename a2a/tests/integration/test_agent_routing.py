"""Integration tests for agent routing and end-to-end flow.

Validates: Requirements 5.1, 6.1
- Full request → response cycle through the entry point
- Missing prompt returns appropriate error
- Exceptions produce sanitized errors
"""

import sys
from unittest.mock import MagicMock, patch

# Mock strands and bedrock_agentcore before importing entrypoint
mock_strands = MagicMock()
mock_strands.tool = lambda func: func


class _MockAgent:
    """Mock Agent that records calls and returns a configurable result."""

    def __init__(self, system_prompt="", tools=None):
        self.system_prompt = system_prompt
        self.tools = tools or []
        self._response = None
        self._side_effect = None

    def __call__(self, prompt):
        if self._side_effect:
            raise self._side_effect
        result = MagicMock()
        result.message = self._response or f"Processed: {prompt}"
        return result


mock_strands.Agent = _MockAgent
sys.modules.setdefault("strands", mock_strands)

# Mock bedrock_agentcore
mock_agentcore = MagicMock()
mock_agentcore_runtime = MagicMock()


class _MockBedrockAgentCoreApp:
    def __init__(self):
        pass

    def entrypoint(self, func):
        return func

    def run(self):
        pass


mock_agentcore_runtime.BedrockAgentCoreApp = _MockBedrockAgentCoreApp
mock_agentcore.runtime = mock_agentcore_runtime
sys.modules.setdefault("bedrock_agentcore", mock_agentcore)
sys.modules.setdefault("bedrock_agentcore.runtime", mock_agentcore_runtime)

# Clear cached imports of entrypoint/agent to pick up our mocks
for mod_name in list(sys.modules.keys()):
    if mod_name in ("agent", "entrypoint"):
        del sys.modules[mod_name]

import pytest


class TestFullRequestResponseCycle:
    """Test end-to-end flow through the entry point with mocked agent."""

    def test_full_request_response_cycle_with_mocked_tools(self):
        """Simulate a payload {"prompt": "restart my-app"}, verify {"result": ...} is returned.

        Validates: Requirements 5.1
        """
        from entrypoint import invoke

        # Patch the module-level agent to return a controlled response
        mock_result = MagicMock()
        mock_result.message = "Application my-app synced successfully"

        with patch("entrypoint.agent", return_value=mock_result) as mock_agent:
            result = invoke({"prompt": "restart my-app"})

        assert "result" in result
        assert result["result"] == "Application my-app synced successfully"
        mock_agent.assert_called_once_with("restart my-app")


class TestEntryPointMissingPrompt:
    """Test that missing prompt returns an appropriate error."""

    def test_entry_point_missing_prompt_returns_error(self):
        """Verify {"error": "No prompt provided..."} for empty payload.

        Validates: Requirements 5.1
        """
        from entrypoint import invoke

        # Empty dict - no prompt key
        result = invoke({})
        assert "error" in result
        assert "No prompt provided" in result["error"]

        # Empty string prompt
        result = invoke({"prompt": ""})
        assert "error" in result
        assert "No prompt provided" in result["error"]

    def test_entry_point_none_prompt_returns_error(self):
        """Verify None prompt also returns error.

        Validates: Requirements 5.1
        """
        from entrypoint import invoke

        result = invoke({"prompt": None})
        assert "error" in result
        assert "No prompt provided" in result["error"]


class TestEntryPointExceptionHandling:
    """Test that exceptions produce sanitized errors."""

    def test_entry_point_exception_returns_sanitized_error(self):
        """Verify exceptions produce sanitized errors without stack traces or internal details.

        Validates: Requirements 5.1
        """
        from entrypoint import invoke

        # Simulate a RuntimeError during agent invocation
        with patch(
            "entrypoint.agent", side_effect=RuntimeError("internal database connection failed")
        ):
            result = invoke({"prompt": "restart my-app"})

        assert "error" in result
        # Should contain failure indication
        assert "failed" in result["error"].lower() or "error" in result["error"].lower()
        # Should NOT contain internal details or stack traces
        assert "Traceback" not in result["error"]
        assert 'File "' not in result["error"]
        assert "internal database connection failed" not in result["error"]

    def test_entry_point_type_error_returns_sanitized_error(self):
        """Verify TypeError is also sanitized.

        Validates: Requirements 5.1
        """
        from entrypoint import invoke

        with patch("entrypoint.agent", side_effect=TypeError("NoneType is not callable")):
            result = invoke({"prompt": "do something"})

        assert "error" in result
        assert "TypeError" in result["error"]
        assert "NoneType is not callable" not in result["error"]
        assert "Traceback" not in result["error"]

    def test_entry_point_value_error_returns_sanitized_error(self):
        """Verify ValueError from config is also sanitized.

        Validates: Requirements 5.1
        """
        from entrypoint import invoke

        with patch(
            "entrypoint.agent",
            side_effect=ValueError("Missing required environment variables: Region"),
        ):
            result = invoke({"prompt": "check cluster status"})

        assert "error" in result
        assert "Traceback" not in result["error"]
        assert 'File "' not in result["error"]
