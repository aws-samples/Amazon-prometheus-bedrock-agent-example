"""Unit tests for the configuration module.

The agent's configuration is fully optional: every field has a sensible
default (Region, ArgoCD_LoadBalancer_URL, EKS_CLUSTER_NAME), so get_config()
never raises even when no environment variables are set.
"""

import pytest

from config import (
    DEFAULT_ARGOCD_URL,
    DEFAULT_CLUSTER,
    DEFAULT_REGION,
    AgentConfig,
    REQUIRED_ENV_VARS,
    get_config,
)

ENV_VARS = ("Region", "ArgoCD_LoadBalancer_URL", "EKS_CLUSTER_NAME")


@pytest.fixture(autouse=True)
def clear_env_vars(monkeypatch):
    """Ensure a clean environment for every test in this module."""
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class TestGetConfigDefaults:
    """Tests for default values when no environment variables are set."""

    def test_returns_agent_config_instance(self):
        """get_config() always returns an AgentConfig instance."""
        assert isinstance(get_config(), AgentConfig)

    def test_defaults_used_when_no_env_vars_set(self):
        """With no env vars set, all fields fall back to module defaults."""
        config = get_config()
        assert config.region == DEFAULT_REGION
        assert config.argocd_url == DEFAULT_ARGOCD_URL
        assert config.eks_cluster_name == DEFAULT_CLUSTER

    def test_required_env_vars_is_empty(self):
        """No environment variables are mandatory in the current design."""
        assert REQUIRED_ENV_VARS == []

    def test_get_config_never_raises_with_no_env_vars(self):
        """get_config() must not raise even when nothing is configured."""
        try:
            get_config()
        except Exception as e:  # pragma: no cover - failure path
            pytest.fail(f"get_config() raised unexpectedly: {e}")


class TestGetConfigFromEnv:
    """Tests for reading configuration from environment variables."""

    def test_returns_agent_config_when_all_vars_set(self, monkeypatch):
        """All three env vars are reflected exactly in the returned config."""
        monkeypatch.setenv("Region", "us-west-2")
        monkeypatch.setenv("ArgoCD_LoadBalancer_URL", "https://argocd.example.com")
        monkeypatch.setenv("EKS_CLUSTER_NAME", "my-cluster")

        config = get_config()

        assert config.region == "us-west-2"
        assert config.argocd_url == "https://argocd.example.com"
        assert config.eks_cluster_name == "my-cluster"

    def test_partial_env_vars_fall_back_to_defaults_for_the_rest(self, monkeypatch):
        """Setting only one env var leaves the others at their defaults."""
        monkeypatch.setenv("EKS_CLUSTER_NAME", "custom-cluster")

        config = get_config()

        assert config.eks_cluster_name == "custom-cluster"
        assert config.region == DEFAULT_REGION
        assert config.argocd_url == DEFAULT_ARGOCD_URL

    def test_empty_string_env_var_is_used_as_is(self, monkeypatch):
        """An explicitly empty string is a valid (falsy but present) value.

        os.environ.get(var, default) only falls back to the default when the
        variable is entirely unset, not when it is set to "".
        """
        monkeypatch.setenv("Region", "")

        config = get_config()

        assert config.region == ""
