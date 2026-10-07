"""Pod Disruption Budget check tool.

Verifies that a PodDisruptionBudget exists for a deployment
before allowing destructive changes.
"""

import json
import logging

from strands import tool

from ..clients.k8s_client import get_k8s_clients

logger = logging.getLogger(__name__)


@tool
def check_pdb(deployment_name: str, namespace: str = "default") -> str:
    """Check if a Pod Disruption Budget exists for a deployment.

    Verifies that a PDB is configured to protect the deployment
    before allowing destructive operations (restart, rollback, scale-down).

    Args:
        deployment_name: Name of the deployment to check.
        namespace: Kubernetes namespace. Defaults to "default".

    Returns:
        JSON string with the check result including:
        - allowed: boolean indicating if destructive changes are permitted
        - reason: explanation of the decision
        - pdb_details: PDB configuration if found
    """
    if not deployment_name or not deployment_name.strip():
        return json.dumps({
            "allowed": False,
            "reason": "deployment_name is required",
            "pdb_details": None,
        })

    deployment_name = deployment_name.strip()
    namespace = namespace.strip() if namespace else "default"

    try:
        core_api, policy_api, apps_api = get_k8s_clients()

        # Step 1: Get the deployment and its label selector
        try:
            deployment = apps_api.read_namespaced_deployment(
                name=deployment_name, namespace=namespace
            )
        except Exception as e:
            if "NotFound" in str(e) or "404" in str(e):
                return json.dumps({
                    "allowed": False,
                    "reason": f"Deployment '{deployment_name}' not found in namespace '{namespace}'",
                    "pdb_details": None,
                })
            raise

        # Get deployment's pod selector labels
        match_labels = deployment.spec.selector.match_labels or {}

        # Step 2: List all PDBs in the namespace
        pdbs = policy_api.list_namespaced_pod_disruption_budget(namespace=namespace)

        # Step 3: Find PDBs that match this deployment's pods
        matching_pdbs = []
        for pdb in pdbs.items:
            pdb_selector = pdb.spec.selector
            if not pdb_selector or not pdb_selector.match_labels:
                continue

            # Check if PDB selector matches deployment's pod labels
            pdb_labels = pdb_selector.match_labels
            if all(match_labels.get(k) == v for k, v in pdb_labels.items()):
                matching_pdbs.append({
                    "name": pdb.metadata.name,
                    "min_available": str(pdb.spec.min_available) if pdb.spec.min_available else None,
                    "max_unavailable": str(pdb.spec.max_unavailable) if pdb.spec.max_unavailable else None,
                    "current_healthy": pdb.status.current_healthy if pdb.status else None,
                    "desired_healthy": pdb.status.desired_healthy if pdb.status else None,
                    "disruptions_allowed": pdb.status.disruptions_allowed if pdb.status else None,
                })

        if matching_pdbs:
            return json.dumps({
                "allowed": True,
                "reason": f"PodDisruptionBudget found for deployment '{deployment_name}'. Destructive changes are allowed.",
                "pdb_details": matching_pdbs,
            })
        else:
            return json.dumps({
                "allowed": False,
                "reason": (
                    f"No PodDisruptionBudget found for deployment '{deployment_name}' "
                    f"in namespace '{namespace}'. Destructive changes are NOT allowed "
                    f"without a PDB in place to ensure availability."
                ),
                "pdb_details": None,
            })

    except Exception as e:
        logger.exception("Error checking PDB")
        return json.dumps({
            "allowed": False,
            "reason": f"Error checking PDB: {str(e)}",
            "pdb_details": None,
        })
