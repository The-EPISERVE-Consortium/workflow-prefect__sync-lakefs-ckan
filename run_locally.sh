#!/bin/bash

# Extract secrets from the cluster
export CKAN_API_TOKEN=$(kubectl get secret ckan-credentials -o jsonpath='{.data.ckan-api-token}' | base64 -d)
export LAKEFS_ACCESS_KEY=$(kubectl get secret lakefs-credentials -o jsonpath='{.data.lakefs-access-key}' | base64 -d)
export LAKEFS_SECRET_KEY=$(kubectl get secret lakefs-credentials -o jsonpath='{.data.lakefs-secret-key}' | base64 -d)

# Set the host vars
export CKAN_HOST=https://ckan.episerve.zib.de
export LAKEFS_HOST=https://lake-episerve.zib.de/
export DOIP_HOST=https://doip.episerve.zib.de

# Run
source .venv/bin/activate

if [[ $# -eq 0 ]]; then
  echo "Usage: run_locally.sh --model-runs|--raw [--force-recreate]"
  echo ""
  echo "  --model-runs      Sync the model-runs repo"
  echo "  --raw             Sync the data-raw repo"
  echo "  --force-recreate  Overwrite datasets that already exist in CKAN"
  exit 0
fi

FORCE=False
REPO=

for arg in "$@"; do
  case "$arg" in
    --force-recreate) FORCE=True ;;
    --model-runs)     REPO=model-runs ;;
    --raw)            REPO=data-raw ;;
    *) echo "Unknown argument: $arg"; exit 1 ;;
  esac
done

if [[ -z "$REPO" ]]; then
  echo "Error: one of --model-runs or --raw is required"
  exit 1
fi

if [[ "$REPO" == "data-raw" ]]; then
python -c "
from tools.lakefs_tools import list_raw_datasets
from flow.sync_ckan_with_lakefs_raw import _do_sync_raw_dataset

repo = 'data-raw'
force_recreate = $FORCE
for fdo_path in list_raw_datasets(repo):
    _do_sync_raw_dataset(fdo_path, repo, force_recreate=force_recreate)
"
else
python -c "
from tools.lakefs_tools import list_runs
from flow.sync_ckan_with_lakefs import _do_sync_run

repo = 'model-runs'
force_recreate = $FORCE
for run_id in list_runs(repo):
    _do_sync_run(run_id, repo, force_recreate=force_recreate)
"
fi
