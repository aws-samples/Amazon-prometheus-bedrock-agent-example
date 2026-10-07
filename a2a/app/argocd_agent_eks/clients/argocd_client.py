"""ArgoCD client using Kubernetes API (CRDs) for EKS-deployed agent."""

import logging
import time

from ..clients.k8s_client import get_k8s_client
from ..config import get_config
from ..exceptions import (
    ApplicationNotFoundError,
    ArgoCDAuthError,
    HelmNotConfiguredError,
)

logger = logging.getLogger(__name__)

ARGOCD_NAMESPACE = "argocd"
ARGOCD_GROUP = "argoproj.io"
ARGOCD_VERSION = "v1alpha1"


def get_argocd_client(argocd_url: str = "", cluster_name: str = "", region: str = "us-east-1") -> "ArgoCD":
    """Create an ArgoCD client using Kubernetes API."""
    config = get_config()
    k8s_api = get_k8s_client(config.eks_cluster_name, config.region)
    return ArgoCD(k8s_api)


class ArgoCD:
    """Manages ArgoCD applications via Kubernetes CRDs."""

    def __init__(self, k8s_api):
        self.api = k8s_api
        logger.info("ArgoCD K8s client initialized")

    def _get_application(self, app_name: str) -> dict:
        try:
            return self.api.get_namespaced_custom_object(
                group=ARGOCD_GROUP, version=ARGOCD_VERSION,
                namespace=ARGOCD_NAMESPACE, plural="applications", name=app_name,
            )
        except Exception as e:
            if "NotFound" in str(e) or "404" in str(e):
                return None
            raise

    def application_exists(self, app_name: str) -> bool:
        return self._get_application(app_name) is not None

    def sync_application(self, app_name: str) -> dict:
        if not self.application_exists(app_name):
            raise ApplicationNotFoundError(f"Application {app_name} not found")

        patch_body = {
            "metadata": {"annotations": {"argocd.argoproj.io/refresh": "hard"}},
            "operation": {
                "initiatedBy": {"username": "agent-automation"},
                "sync": {
                    "prune": True, "force": True,
                    "syncStrategy": {"apply": {"force": True}},
                    "syncOptions": ["Replace=true", "Force=true"],
                },
            },
        }
        try:
            self.api.patch_namespaced_custom_object(
                group=ARGOCD_GROUP, version=ARGOCD_VERSION,
                namespace=ARGOCD_NAMESPACE, plural="applications",
                name=app_name, body=patch_body,
            )
            return {"status": "success", "message": f"Application {app_name} synced successfully"}
        except Exception as e:
            raise ArgoCDAuthError(f"Failed to sync application: {str(e)}") from e

    def rollback_application(self, app_name: str) -> dict:
        if not self.application_exists(app_name):
            raise ApplicationNotFoundError(f"Application {app_name} not found")

        app = self._get_application(app_name)
        history = app.get("status", {}).get("history", [])
        if len(history) < 2:
            raise ValueError("No previous revision available for rollback")

        previous_revision = history[-2]
        revision = previous_revision.get("revision", "")

        patch_body = {
            "operation": {
                "initiatedBy": {"username": "agent-automation"},
                "sync": {"revision": revision, "prune": True, "syncStrategy": {"apply": {"force": True}}},
            },
        }
        try:
            self.api.patch_namespaced_custom_object(
                group=ARGOCD_GROUP, version=ARGOCD_VERSION,
                namespace=ARGOCD_NAMESPACE, plural="applications",
                name=app_name, body=patch_body,
            )
            return {"status": "success", "message": f"Application {app_name} rolled back successfully", "revision_id": revision}
        except Exception as e:
            raise ArgoCDAuthError(f"Failed to rollback application: {str(e)}") from e

    def patch_resource_memory(self, app_name: str, resource_name: str, memory_limit: str, memory_request: str) -> dict:
        app = self._get_application(app_name)
        if app is None:
            raise ApplicationNotFoundError(f"Application {app_name} not found")

        spec = app.get("spec", {})
        helm_source = self._find_helm_source(spec)
        if helm_source is None:
            raise HelmNotConfiguredError(f"Application {app_name} is not configured with a Helm source")

        camel_name = self._to_camel_case(resource_name)
        parameters = helm_source.setdefault("parameters", [])
        parameters[:] = [p for p in parameters if not (p.get("name", "").startswith(f"{resource_name}.") or p.get("name", "").startswith(f"{camel_name}."))]
        parameters.append({"name": f"{camel_name}.resources.limits.memory", "value": memory_limit})
        parameters.append({"name": f"{camel_name}.resources.requests.memory", "value": memory_request})

        sync_policy = spec.setdefault("syncPolicy", {})
        sync_options = sync_policy.setdefault("syncOptions", [])
        for opt in ["ServerSideApply=true"]:
            if opt not in sync_options:
                sync_options.append(opt)

        try:
            dest_namespace = app.get("spec", {}).get("destination", {}).get("namespace", "default")

            # Step 1: Update Helm params with new memory values
            self.api.patch_namespaced_custom_object(
                group=ARGOCD_GROUP, version=ARGOCD_VERSION,
                namespace=ARGOCD_NAMESPACE, plural="applications",
                name=app_name, body={"spec": spec},
            )
            logger.info(f"Helm params updated for {app_name}")

            # Step 2: Sync with ServerSideApply — handles immutable Pod fields.
            # Short pause to let the app controller register the new desired
            # state; ArgoCD reconciliation itself is asynchronous, so we keep
            # this off the critical path as much as possible to stay well under
            # the API Gateway 29s integration timeout.
            time.sleep(1)
            sync_patch = {
                "operation": {
                    "initiatedBy": {"username": "agent-automation"},
                    "sync": {
                        "prune": True,
                        "syncStrategy": {"apply": {"force": True}},
                        "syncOptions": ["ServerSideApply=true", "Force=true"],
                    },
                },
            }
            self.api.patch_namespaced_custom_object(
                group=ARGOCD_GROUP, version=ARGOCD_VERSION,
                namespace=ARGOCD_NAMESPACE, plural="applications",
                name=app_name, body=sync_patch,
            )
            logger.info(f"Triggered ArgoCD sync with ServerSideApply for {app_name}")

            # Step 3: Trigger full sync to recreate the pod with new limits.
            # ArgoCD applies and reconciles asynchronously; the DevOps Agent
            # skill re-runs diagnostics after ~90s to verify, so we do not
            # block here waiting for reconciliation to finish.
            time.sleep(2)
            self.sync_application(app_name)

            return {"status": "success", "message": f"Successfully updated memory for {app_name}: {resource_name} limits={memory_limit}, requests={memory_request}"}
        except Exception as e:
            raise ArgoCDAuthError(f"Failed to patch application: {str(e)}") from e

    @staticmethod
    def _to_camel_case(name: str) -> str:
        parts = name.split("-")
        return parts[0].lower() + "".join(p.capitalize() for p in parts[1:]) if parts else ""

    @staticmethod
    def _find_helm_source(spec: dict):
        for source in spec.get("sources", []):
            if source.get("helm") is not None:
                return source["helm"]
        source = spec.get("source", {})
        return source.get("helm")
