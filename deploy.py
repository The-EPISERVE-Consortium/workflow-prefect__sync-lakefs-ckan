"""
Register the sync_ckan_with_lakefs flow as a Prefect deployment.

Run once (or on every release) from inside the cluster or any machine that
can reach the Prefect server:

    PREFECT_API_URL=http://prefect-server.default.svc.cluster.local:4200/api \
        python deploy.py
"""

import os

from prefect.runner.storage import GitRepository

from flow.sync_ckan_with_lakefs import sync_ckan_with_lakefs

GITHUB_REPO_URL = "https://github.com/The-EPISERVE-Consortium/workflow-prefect__sync-lakefs-ckan"
DOCKER_IMAGE    = "ghcr.io/the-episerve-consortium/sync-ckan-with-lakefs:latest"
WORK_POOL_NAME  = os.getenv("WORK_POOL_NAME", "kubernetes-pool")
DEPLOYMENT_NAME = os.getenv("DEPLOYMENT_NAME", "sync-ckan-with-lakefs")

if __name__ == "__main__":
    sync_ckan_with_lakefs.from_source(
        source=GitRepository(url=GITHUB_REPO_URL, branch="main"),
        entrypoint="flow/sync_ckan_with_lakefs.py:sync_ckan_with_lakefs",
    ).deploy(
        name=DEPLOYMENT_NAME,
        work_pool_name=WORK_POOL_NAME,
        job_variables={
            "image": DOCKER_IMAGE,
            "image_pull_policy": "Always",
        },
        parameters={
            "lakefs_run_repo": os.getenv("LAKEFS_RUN_REPO", "model-runs"),
        },
    )
