#!/bin/bash

# Extract secrets from the cluster
export CKAN_API_TOKEN=$(kubectl get secret ckan-credentials -o jsonpath='{.data.ckan-api-token}' | base64 -d)
export LAKEFS_ACCESS_KEY=$(kubectl get secret lakefs-credentials -o jsonpath='{.data.lakefs-access-key}' | base64 -d)
export LAKEFS_SECRET_KEY=$(kubectl get secret lakefs-credentials -o jsonpath='{.data.lakefs-secret-key}' | base64 -d)

# Set the host vars
export CKAN_HOST=https://ckan.episerve.zib.de
export LAKEFS_HOST=https://lake-episerve.zib.de/

# Run
source .venv/bin/activate

FORCE=False
[[ "$1" == "--force-recreate" ]] && FORCE=True

python -c "
from tools.lakefs_tools import list_runs
from flow.sync_ckan_with_lakefs import _do_sync_run

repo = 'model-runs'
force_recreate = $FORCE
for run_id in list_runs(repo):
    _do_sync_run(run_id, repo, force_recreate=force_recreate)
"
