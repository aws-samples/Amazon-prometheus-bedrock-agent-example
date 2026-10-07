"""Unit tests for agent definition and entry point.

Validates: Requirements 4.1, 4.2, 5.1, 5.7
"""

import sys
from unittest.mock import MagicMock, patch

# Mock strands module before importing agent/entrypoint
mock_strands = MagicMock()
mock_strands.tool = lambda func: func


class MockAgent:
    def __init__(self, system_prompt="", tools=None):
        self.system_prompt = system_prompt
        self.tools = tools or []


mock_strands.Agent = MockAgent
sys.modules['strands'] = mock_strands

# Mock bedrock_agentcore with a pass-through entrypoint decorator
mock_agentcore = MagicMock()
mock_agentcore_runtime = MagicMock()


class MockBedrockAgentCoreApp:
    def __init__(self):
        pass

    def entrypoint(self, func):
        """Pass-through decorator that preserves the original function."""
        return func

    def run(self):
        pass


mock_agentcore_runtime.BedrockAgentCoreApp = MockBedrockAgentCoreApp
mock_agentcore.runtime = mock_agentcore_runtime
sys.modules['bedrock_agentcore'] = mock_agentcore
sys.modules['bedrock_agentcore.runtime'] = mock_agentcore_runtime


# Force re-import of agent module to pick up our mocks
for mod_name in list(sys.modules.keys()):
    if mod_name in ('agent', 'entrypoint'):
        del sys.modules[mod_name]

from agent import create_agent, SYSTEM_PROMPT  # noqa: E402


class TestCreateAgent:
    """Tests for the create_agent() function."""

    def test_create_agent_has_system_prompt_with_operations(self):
        """Verify SYSTEM_PROMPT mentions all three operations.

        Validates: Requirements 4.1
        """
        agent = create_agent()
        assert "Restart/Rollback" in agent.system_prompt
        assert "Memory Adjustment" in agent.system_prompt
        assert "K8sGPT Diagnostics" in agent.system_prompt

    def test_create_agent_registers_three_tools(self):
        """Verify agent has all three tools registered.

        Validates: Requirements 4.2
        """
        agent = create_agent()
        assert len(agent.tools) == 3


class TestEntryPointInvoke:
    """Tests for the entrypoint invoke function."""

    def test_returns_error_for_missing_prompt(self):
        """Call invoke with empty prompt, verify error response.

        Validates: Requirements 5.1
        """
        from entrypoint import invoke

        result = invoke({"prompt": ""})
        assert "error" in result
        assert "No prompt provided" in result["error"]

    def test_returns_error_for_empty_payload(self):
        """Call invoke with empty dict, verify error returned.

        Validates: Requirements 5.1
        """
        from entrypoint import invoke

        result = invoke({})
        assert "error" in result
        assert "No prompt provided" in result["error"]

    def test_catches_exceptions_without_stack_traces(self):
        """Mock agent to raise RuntimeError, verify error response doesn't
        contain stack trace indicators.

        Validates: Requirements 5.7
        """
        from entrypoint import invoke

        # Patch the module-level agent in entrypoint to raise on call
        with patch('entrypoint.agent', side_effect=RuntimeError("something broke")):
            result = invoke({"prompt": "do something"})

        assert "error" in result
        assert "Traceback" not in result["error"]
        assert 'File "' not in result["error"]
        assert "something broke" not in result["error"]
