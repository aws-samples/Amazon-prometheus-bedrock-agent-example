"""Property-based tests for tool validation and configuration logic."""

import sys
from unittest.mock import MagicMock

from hypothesis import given, settings
from hypothesis import strategies as st

# Mock the strands module before importing the tool so @tool is a passthrough
mock_strands = MagicMock()
mock_strands.tool = lambda func: func
sys.modules.setdefault("strands", mock_strands)

# Mock dependent modules that won't be available in test environment
sys.modules.setdefault("clients.argocd_client", MagicMock())
sys.modules.setdefault(
    "exceptions",
    MagicMock(
        ApplicationNotFoundError=Exception,
        ArgoCDAuthError=Exception,
        HelmNotConfiguredError=Exception,
    ),
)

from tools.restart_rollback import restart_rollback_argocd  # noqa: E402


# Feature: strands-agent-argocd, Property 1: Invalid action rejection
class TestInvalidActionRejection:
    """Property tests for restart_rollback_argocd invalid action handling.

    **Validates: Requirements 1.4**

    For any string that is not "restart" or "rollback", invoking the
    Restart_Rollback_Tool with that string as the action_name SHALL return
    an error message that contains both "restart" and "rollback" as the
    valid actions.
    """

    @given(
        action=st.text().filter(
            lambda s: s.strip() not in ("restart", "rollback")
        ).filter(
            lambda s: len(s.strip()) > 0  # non-empty to avoid missing param error
        )
    )
    @settings(max_examples=100)
    def test_invalid_action_returns_error_with_valid_actions(self, action: str) -> None:
        """Any invalid action returns an error mentioning both valid actions."""
        result = restart_rollback_argocd(app_name="test-app", action_name=action)
        assert "Error" in result, (
            f"Expected error for invalid action '{action}', got: {result}"
        )
        assert "restart" in result, (
            f"Error message should mention 'restart' as valid action, got: {result}"
        )
        assert "rollback" in result, (
            f"Error message should mention 'rollback' as valid action, got: {result}"
        )

    @given(
        action=st.text().filter(
            lambda s: s.strip() not in ("restart", "rollback")
        ).filter(
            lambda s: len(s.strip()) > 0
        )
    )
    @settings(max_examples=100)
    def test_invalid_action_does_not_proceed_with_operation(self, action: str) -> None:
        """Invalid action returns immediately without attempting the operation."""
        result = restart_rollback_argocd(app_name="test-app", action_name=action)
        # The result should be an error string, not a success message
        assert result.startswith("Error:"), (
            f"Expected result to start with 'Error:' for invalid action '{action}', "
            f"got: {result}"
        )


# ---------------------------------------------------------------------------
# Property: Configuration always resolves to a valid AgentConfig
# ---------------------------------------------------------------------------

import os  # noqa: E402

# Remove the mock for 'config' so we can import the real module
sys.modules.pop("config", None)
import config as real_config  # noqa: E402

get_config = real_config.get_config
ENV_VARS = ("Region", "ArgoCD_LoadBalancer_URL", "EKS_CLUSTER_NAME")


# Feature: strands-agent-argocd, Property: Configuration round-trips env vars
class TestConfigRoundTripsEnvironmentVariables:
    """Property tests for the configuration module's env-var handling.

    Every configuration field has a default, so get_config() never raises.
    For any combination of values assigned to Region, ArgoCD_LoadBalancer_URL,
    and EKS_CLUSTER_NAME, the returned AgentConfig SHALL reflect exactly the
    values that were set, and SHALL fall back to the module defaults for any
    variable that was left unset.
    """

    # os.environ cannot hold embedded null bytes, so exclude them - that's an
    # OS-level constraint, not a property of our config module.
    env_safe_text = lambda **kw: st.text(**kw).filter(lambda s: "\x00" not in s)

    @given(
        region=st.one_of(st.none(), env_safe_text(min_size=1, max_size=20)),
        argocd_url=st.one_of(st.none(), env_safe_text(min_size=1, max_size=50)),
        cluster=st.one_of(st.none(), env_safe_text(min_size=1, max_size=20)),
    )
    @settings(max_examples=100)
    def test_get_config_reflects_set_vars_and_defaults_the_rest(
        self, region, argocd_url, cluster
    ) -> None:
        """get_config() reflects whichever env vars are set, defaulting the rest."""
        env_backup = {var: os.environ.get(var) for var in ENV_VARS}
        try:
            for var in ENV_VARS:
                os.environ.pop(var, None)

            if region is not None:
                os.environ["Region"] = region
            if argocd_url is not None:
                os.environ["ArgoCD_LoadBalancer_URL"] = argocd_url
            if cluster is not None:
                os.environ["EKS_CLUSTER_NAME"] = cluster

            config = get_config()

            assert config.region == (region if region is not None else real_config.DEFAULT_REGION)
            assert config.argocd_url == (
                argocd_url if argocd_url is not None else real_config.DEFAULT_ARGOCD_URL
            )
            assert config.eks_cluster_name == (
                cluster if cluster is not None else real_config.DEFAULT_CLUSTER
            )
        finally:
            for var, original in env_backup.items():
                if original is None:
                    os.environ.pop(var, None)
                else:
                    os.environ[var] = original

    @given(
        region=env_safe_text(min_size=0, max_size=20),
        argocd_url=env_safe_text(min_size=0, max_size=50),
        cluster=env_safe_text(min_size=0, max_size=20),
    )
    @settings(max_examples=100)
    def test_get_config_never_raises_for_any_string_value(
        self, region: str, argocd_url: str, cluster: str
    ) -> None:
        """get_config() never raises, regardless of what the env vars are set to."""
        env_backup = {var: os.environ.get(var) for var in ENV_VARS}
        try:
            os.environ["Region"] = region
            os.environ["ArgoCD_LoadBalancer_URL"] = argocd_url
            os.environ["EKS_CLUSTER_NAME"] = cluster

            config = get_config()

            assert config.region == region
            assert config.argocd_url == argocd_url
            assert config.eks_cluster_name == cluster
        finally:
            for var, original in env_backup.items():
                if original is None:
                    os.environ.pop(var, None)
                else:
                    os.environ[var] = original
