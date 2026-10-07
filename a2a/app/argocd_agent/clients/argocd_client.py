"""ArgoCD client module using Kubernetes API for EKS ArgoCD Capability.

Since the EKS ArgoCD Capability's external endpoint requires SSO session auth
(not compatible with programmatic tokens), this client manages ArgoCD
applications directly via Kubernetes custom resources (CRDs).

This approach uses the same IAM-based EKS authentication that works for
all other K8s operations.
"""

import json
import logging
import time

from kubernetes import client as k8s_client

from clients.k8s_client import get_k8s_client
from config import get_config
from exceptions import (
    ApplicationNotFoundError,
    ArgoCDAuthError,
    HelmNotConfiguredError,
)

logger = logging.getLogger(__name__)

ARGOCD_NAMESPACE = "argocd"
ARGOCD_GROUP = "argoproj.io"
ARGOCD_VERSION = "v1alpha1"


def get_argocd_client(argocd_url: str = "", cluster_name: str = "", region: str = "us-east-1") -> "ArgoCD":
    """Create an ArgoCD client using Kubernetes API.

    Connects directly to the EKS cluster and manages ArgoCD Application
    custom resources — bypasses the SSO-protected external endpoint.

    Args:
        argocd_url: Unused (kept for compatibility).
        cluster_name: EKS cluster name for authentication.
        region: AWS region.

    Returns:
        An ArgoCD client instance.
    """
    config = get_config()
    k8s_api = get_k8s_client(config.eks_cluster_name, config.region)
    return ArgoCD(k8s_api)


class ArgoCD:
    """Client for managing ArgoCD applications via Kubernetes CRDs.

    Instead of hitting the ArgoCD REST API (which requires SSO on
    EKS Capability), this manages Application CRDs directly through
    the Kubernetes API.
    """

    def __init__(self, k8s_api):
        """Initialize with a Kubernetes CustomObjectsApi client.

        Args:
            k8s_api: A configured kubernetes.client.CustomObjectsApi instance.
        """
        self.api = k8s_api
        logger.info("ArgoCD K8s client initialized")

    def _get_application(self, app_name: str) -> dict:
        """Get an ArgoCD Application custom resource."""
        try:
            return self.api.get_namespaced_custom_object(
                group=ARGOCD_GROUP,
                version=ARGOCD_VERSION,
                namespace=ARGOCD_NAMESPACE,
                plural="applications",
                name=app_name,
            )
        except Exception as e:
            if "NotFound" in str(e) or "404" in str(e):
                return None
            raise

    def _list_applications(self) -> list:
        """List all ArgoCD Application custom resources."""
        result = self.api.list_namespaced_custom_object(
            group=ARGOCD_GROUP,
            version=ARGOCD_VERSION,
            namespace=ARGOCD_NAMESPACE,
            plural="applications",
        )
        return result.get("items", [])

    def application_exists(self, app_name: str) -> bool:
        """Check whether an application exists in ArgoCD."""
        app = self._get_application(app_name)
        return app is not None

    def sync_application(self, app_name: str) -> dict:
        """Sync (restart) an ArgoCD application.

        Uses Replace strategy to handle immutable pod field updates.
        """
        if not self.application_exists(app_name):
            raise ApplicationNotFoundError(f"Application {app_name} not found")

        # Patch the Application to trigger a sync operation with Replace strategy
        patch_body = {
            "metadata": {
                "annotations": {
                    "argocd.argoproj.io/refresh": "hard"
                }
            },
            "operation": {
                "initiatedBy": {"username": "agent-automation"},
                "sync": {
                    "prune": True,
                    "force": True,
                    "syncStrategy": {
                        "apply": {"force": True}
                    },
                    "syncOptions": ["Replace=true", "Force=true"],
                },
            },
        }

        try:
            self.api.patch_namespaced_custom_object(
                group=ARGOCD_GROUP,
                version=ARGOCD_VERSION,
                namespace=ARGOCD_NAMESPACE,
                plural="applications",
                name=app_name,
                body=patch_body,
            )
            return {"status": "success", "message": f"Application {app_name} synced successfully"}
        except Exception as e:
            raise ArgoCDAuthError(f"Failed to sync application: {str(e)}") from e

    def rollback_application(self, app_name: str) -> dict:
        """Rollback an ArgoCD application to its previous revision."""
        if not self.application_exists(app_name):
            raise ApplicationNotFoundError(f"Application {app_name} not found")

        app = self._get_application(app_name)
        history = app.get("status", {}).get("history", [])

        if len(history) < 2:
            raise ValueError("No previous revision available for rollback")

        # Get previous revision
        previous_revision = history[-2]  # Second-to-last in history
        revision = previous_revision.get("revision", "")

        # Patch to sync to the previous revision
        patch_body = {
            "operation": {
                "initiatedBy": {"username": "agent-automation"},
                "sync": {
                    "revision": revision,
                    "prune": True,
                    "syncStrategy": {
                        "apply": {"force": True}
                    },
                },
            },
        }

        try:
            self.api.patch_namespaced_custom_object(
                group=ARGOCD_GROUP,
                version=ARGOCD_VERSION,
                namespace=ARGOCD_NAMESPACE,
                plural="applications",
                name=app_name,
                body=patch_body,
            )
            return {
                "status": "success",
                "message": f"Application {app_name} rolled back successfully",
                "revision_id": revision,
            }
        except Exception as e:
            raise ArgoCDAuthError(f"Failed to rollback application: {str(e)}") from e

    def patch_resource_memory(
        self, app_name: str, resource_name: str, memory_limit: str, memory_request: str
    ) -> dict:
        """Update memory resources for a Helm-managed application."""
        app = self._get_application(app_name)
        if app is None:
            raise ApplicationNotFoundError(f"Application {app_name} not found")

        spec = app.get("spec", {})
        helm_source = self._find_helm_source(spec)
        if helm_source is None:
            raise HelmNotConfiguredError(
                f"Application {app_name} is not configured with a Helm source"
            )

        camel_name = self._to_camel_case(resource_name)
        parameters = helm_source.setdefault("parameters", [])
        parameters[:] = [
            p for p in parameters
            if not (p.get("name", "").startswith(f"{resource_name}.")
                    or p.get("name", "").startswith(f"{camel_name}."))
        ]
        parameters.append({"name": f"{camel_name}.resources.limits.memory", "value": memory_limit})
        parameters.append({"name": f"{camel_name}.resources.requests.memory", "value": memory_request})

        # Ensure syncPolicy uses Force+Replace to handle immutable pod fields
        sync_policy = spec.setdefault("syncPolicy", {})
        sync_options = sync_policy.setdefault("syncOptions", [])
        for opt in ["Replace=true", "Force=true"]:
            if opt not in sync_options:
                sync_options.append(opt)

        # Patch the application spec
        try:
            self.api.patch_namespaced_custom_object(
                group=ARGOCD_GROUP,
                version=ARGOCD_VERSION,
                namespace=ARGOCD_NAMESPACE,
                plural="applications",
                name=app_name,
                body={"spec": spec},
            )
            # Delete existing pods managed by this app before sync
            # This handles bare Pod resources that can't be patched in-place
            try:
                app = self._get_application(app_name)
                dest_namespace = app.get("spec", {}).get("destination", {}).get("namespace", "default")
                config = get_config()
                core_api = k8s_client.CoreV1Api(
                    k8s_client.ApiClient(
                        self.api.api_client.configuration
                    )
                )
                pods = core_api.list_namespaced_pod(
                    namespace=dest_namespace,
                    label_selector=f"app.kubernetes.io/instance={app_name}",
                )
                for pod in pods.items:
                    core_api.delete_namespaced_pod(
                        name=pod.metadata.name,
                        namespace=dest_namespace,
                    )
                    logger.info(f"Deleted pod {pod.metadata.name} for replacement")
            except Exception as e:
                logger.warning(f"Could not pre-delete pods (may be fine): {e}")

            # Trigger sync after patching
            time.sleep(2)
            self.sync_application(app_name)
            return {
                "status": "success",
                "message": (
                    f"Successfully updated memory resources for {app_name}: "
                    f"{resource_name} limits={memory_limit}, requests={memory_request}"
                ),
            }
        except Exception as e:
            raise ArgoCDAuthError(f"Failed to patch application: {str(e)}") from e

    @staticmethod
    def _to_camel_case(hyphenated_name: str) -> str:
        """Convert a hyphenated name to camelCase."""
        parts = hyphenated_name.split("-")
        if not parts:
            return ""
        result = parts[0].lower()
        for part in parts[1:]:
            if part:
                result += part[0].upper() + part[1:]
        return result

    @staticmethod
    def _find_helm_source(spec: dict):
        """Find the Helm source configuration in an application spec."""
        sources = spec.get("sources", [])
        for source in sources:
            helm = source.get("helm")
            if helm is not None:
                return helm
        source = spec.get("source", {})
        helm = source.get("helm")
        if helm is not None:
            return helm
        return None
