"""
Create or update the sync_ckan_with_lakefs Prefect deployment.

Run from the project root after setting PREFECT_API_URL, PREFECT_API_KEY,
CKAN_API_TOKEN, LAKEFS_ACCESS_KEY, and LAKEFS_SECRET_KEY in the environment.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "flows"))

from sync_ckan_with_lakefs import sync_ckan_with_lakefs

if __name__ == "__main__":
    sync_ckan_with_lakefs.deploy(
        name            = "sync-ckan-with-lakefs",
        work_pool_name  = "kubernetes-pool",
        image           = "ghcr.io/the-episerve-consortium/sync-ckan-with-lakefs:latest",
        build           = False,
        push            = False,
        cron            = "0 * * * *",
        job_variables   = {
            "env": {
                "CKAN_API_TOKEN":    os.environ["CKAN_API_TOKEN"],
                "LAKEFS_ACCESS_KEY": os.environ["LAKEFS_ACCESS_KEY"],
                "LAKEFS_SECRET_KEY": os.environ["LAKEFS_SECRET_KEY"],
            }
        },
    )
