"""Property-based tests for security properties.

The ArgoCD agent authenticates to ArgoCD/EKS entirely via IAM (STS-signed
bearer tokens), so there are no long-lived application credentials (no
username/password, no Secrets Manager secret) flowing through the
restart/rollback and memory-adjustment tools. The relevant security
properties for this design are:

1. Unexpected/unknown exceptions never leak their raw message to the caller
   (only domain exceptions with a curated .message are surfaced verbatim).
2. The AgentCore entry point never leaks stack traces for any exception.
"""

import sys
from unittest.mock import MagicMock, patch

# Mock strands before importing tool modules
mock_strands = MagicMock()
mock_strands.tool = lambda func: func
sys.modules.setdefault("strands", mock_strands)

from hypothesis import given, settings
from hypothesis import strategies as st

from tools.restart_rollback import restart_rollback_argocd
from tools.memory_adjustment import adjust_memory


# Feature: strands-agent-argocd, Property: No unexpected-exception leakage
class TestNoUnexpectedExceptionDetailsLeak:
    """Property tests verifying unexpected exceptions never leak their message.

    For any exception message raised by the underlying ArgoCD client that is
    NOT one of the agent's known domain exceptions (ApplicationNotFoundError,
    ArgoCDAuthError, HelmNotConfiguredError, ValueError), the tool SHALL
    return the fixed generic error string and SHALL NOT include any part of
    the original exception message.
    """

    GENERIC_MESSAGE = "Error: An unexpected error occurred while processing the request"

    @given(raw_message=st.text(min_size=1, max_size=200))
    @settings(max_examples=100)
    @patch("tools.restart_rollback.validate_action", return_value=None)
    @patch("tools.restart_rollback.get_argocd_client")
    @patch("tools.restart_rollback.get_config")
    def test_restart_rollback_never_leaks_unexpected_exception_message(
        self,
        mock_get_config: MagicMock,
        mock_get_client: MagicMock,
        mock_validate: MagicMock,
        raw_message: str,
    ) -> None:
        """restart_rollback_argocd always returns the generic message for unexpected errors."""
        mock_get_config.return_value = MagicMock(
            region="us-east-1", argocd_url="https://argocd.example.com", eks_cluster_name="c"
        )
        mock_client = MagicMock()
        mock_client.sync_application.side_effect = RuntimeError(raw_message)
        mock_get_client.return_value = mock_client

        result = restart_rollback_argocd(app_name="test-app", action_name="restart")

        assert result == self.GENERIC_MESSAGE, (
            f"Expected generic error message, got: '{result}'"
        )

    @given(raw_message=st.text(min_size=1, max_size=200))
    @settings(max_examples=100)
    @patch("tools.memory_adjustment.validate_action", return_value=None)
    @patch("tools.memory_adjustment.get_argocd_client")
    @patch("tools.memory_adjustment.get_config")
    def test_memory_adjustment_never_leaks_unexpected_exception_message(
        self,
        mock_get_config: MagicMock,
        mock_get_client: MagicMock,
        mock_validate: MagicMock,
        raw_message: str,
    ) -> None:
        """adjust_memory always returns the generic message for unexpected errors."""
        mock_get_config.return_value = MagicMock(
            region="us-east-1", argocd_url="https://argocd.example.com", eks_cluster_name="c"
        )
        mock_client = MagicMock()
        mock_client.patch_resource_memory.side_effect = RuntimeError(raw_message)
        mock_get_client.return_value = mock_client

        result = adjust_memory(app_name="test-app", pod_name="my-pod", memory_limit="512Mi")

        assert result == self.GENERIC_MESSAGE, (
            f"Expected generic error message, got: '{result}'"
        )


# Feature: strands-agent-argocd, Property: No stack trace leakage in entry point errors
class TestNoStackTraceLeakageInEntryPoint:
    """Property tests for stack trace leakage prevention in the entry point.

    **Validates: Requirements 5.7**

    For any unhandled exception raised during request processing in the runtime
    entry point, the returned error response SHALL NOT contain stack trace
    indicators (such as 'Traceback', 'File "', or line number references)
    while still containing a failure reason description.
    """

    STACK_TRACE_INDICATORS = [
        "Traceback",
        'File "',
        ", line ",
    ]

    EXCEPTION_TYPES = [
        ValueError,
        RuntimeError,
        TypeError,
        KeyError,
        AttributeError,
        IOError,
        ConnectionError,
        TimeoutError,
        PermissionError,
        NotImplementedError,
    ]

    @given(
        exception_type=st.sampled_from(EXCEPTION_TYPES),
        error_message=st.text(min_size=1, max_size=100),
    )
    @settings(max_examples=100)
    def test_no_stack_trace_in_error_response(
        self,
        exception_type: type,
        error_message: str,
    ) -> None:
        """Entry point error responses never contain stack trace indicators."""
        mock_app_instance = MagicMock()
        mock_app_instance.entrypoint = lambda func: func  # decorator passthrough

        mock_runtime = MagicMock()
        mock_runtime.BedrockAgentCoreApp.return_value = mock_app_instance

        mock_agent_instance = MagicMock()
        mock_agent_instance.side_effect = exception_type(error_message)

        mock_agent_module = MagicMock()
        mock_agent_module.create_agent.return_value = mock_agent_instance

        with patch.dict(
            sys.modules,
            {
                "bedrock_agentcore": MagicMock(),
                "bedrock_agentcore.runtime": mock_runtime,
                "strands": MagicMock(),
                "agent": mock_agent_module,
            },
        ):
            # Force re-import of entrypoint to pick up our mocks
            if "entrypoint" in sys.modules:
                del sys.modules["entrypoint"]

            import entrypoint

            # Call the invoke function with a valid payload
            result = entrypoint.invoke({"prompt": "test message"})

        # The result must be a dict with an "error" key
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "error" in result, f"Expected 'error' key in result: {result}"

        error_value = result["error"]

        # Verify no stack trace indicators are present
        for indicator in self.STACK_TRACE_INDICATORS:
            assert indicator not in error_value, (
                f"Stack trace indicator '{indicator}' found in error response: "
                f"'{error_value}'"
            )

        # Verify the error still contains a meaningful failure reason
        assert len(error_value) > 0, "Error message should not be empty"

    @given(
        exception_type=st.sampled_from(EXCEPTION_TYPES),
        error_message=st.text(min_size=1, max_size=100),
    )
    @settings(max_examples=100)
    def test_error_response_contains_failure_type(
        self,
        exception_type: type,
        error_message: str,
    ) -> None:
        """Entry point error responses contain exception type name as failure reason."""
        mock_app_instance = MagicMock()
        mock_app_instance.entrypoint = lambda func: func

        mock_runtime = MagicMock()
        mock_runtime.BedrockAgentCoreApp.return_value = mock_app_instance

        mock_agent_instance = MagicMock()
        mock_agent_instance.side_effect = exception_type(error_message)

        mock_agent_module = MagicMock()
        mock_agent_module.create_agent.return_value = mock_agent_instance

        with patch.dict(
            sys.modules,
            {
                "bedrock_agentcore": MagicMock(),
                "bedrock_agentcore.runtime": mock_runtime,
                "strands": MagicMock(),
                "agent": mock_agent_module,
            },
        ):
            if "entrypoint" in sys.modules:
                del sys.modules["entrypoint"]

            import entrypoint

            result = entrypoint.invoke({"prompt": "test message"})

        # Verify the error response contains a descriptive reason
        # (the implementation includes the exception type name)
        error_value = result["error"]
        assert "failed" in error_value.lower() or exception_type.__name__ in error_value, (
            f"Error response '{error_value}' does not contain failure reason "
            f"(expected 'failed' or '{exception_type.__name__}')"
        )
