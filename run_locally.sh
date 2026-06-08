#!/bin/bash

# Extract secrets from the cluster
export CKAN_API_TOKEN=$(kubectl get secret ckan-credentials -o jsonpath='{.data.ckan-api-token}' | base64 -d)
export LAKEFS_ACCESS_KEY=$(kubectl get secret lakefs-credentials -o jsonpath='{.data.lakefs-access-key}' | base64 -d)
export LAKEFS_SECRET_KEY=$(kubectl get secret lakefs-credentials -o jsonpath='{.data.lakefs-secret-key}' | base64 -d)

# Set the host vars
export CKAN_HOST=https://data.episerve.zib.de
export LAKEFS_HOST=https://lake-episerve.zib.de/
export DOIP_HOST=https://doip.episerve.zib.de
export LAKEFS_MODELS_REPO=models

# Run
if [ ! -d .venv ]; then
    echo "Creating virtual environment..."
    python -m venv .venv
    .venv/bin/pip install -q -r requirements.txt
fi

source .venv/bin/activate

if [[ $# -eq 0 ]]; then
  echo "Usage: run_locally.sh --model-runs|--processed|--sync-models|--register-model <image> [<image> ...] [--force-recreate] [--update]"
  echo ""
  echo "  --model-runs        Sync model-runs lakeFS repo → CKAN (also writes model FDOs to lakeFS models repo)"
  echo "  --processed         Sync data-processed lakeFS repo → CKAN"
  echo "  --sync-models       Sync all model FDOs from lakeFS models repo → CKAN"
  echo "  --register-model    Write FDO to lakeFS models repo and register in CKAN for one or more docker image URIs"
  echo "  --force-recreate    Overwrite items that already exist in CKAN / lakeFS"
  echo "  --update            Patch changed fields for datasets already in CKAN (mutually exclusive with --force-recreate)"
  echo ""
  echo "Examples:"
  echo "  run_locally.sh --model-runs"
  echo "  run_locally.sh --processed --force-recreate"
  echo "  run_locally.sh --processed --update"
  echo "  run_locally.sh --sync-models"
  echo "  run_locally.sh --register-model ghcr.io/the-episerve-consortium/model__prediction__grippeweb__baseline-nullmodel"
  exit 0
fi

FORCE=False
UPDATE=False
MODE=
IMAGES=()

for arg in "$@"; do
  case "$arg" in
    --force-recreate)  FORCE=True ;;
    --update)          UPDATE=True ;;
    --model-runs)      MODE=model-runs ;;
    --processed)       MODE=data-processed ;;
    --sync-models)     MODE=sync-models ;;
    --register-model)  MODE=register-model ;;
    --models)          MODE=register-model ;;  # backwards-compat alias
    *)
      if [[ "$MODE" == "register-model" ]]; then
        IMAGES+=("$arg")
      else
        echo "Unknown argument: $arg"; exit 1
      fi
      ;;
  esac
done

if [[ "$FORCE" == "True" && "$UPDATE" == "True" ]]; then
  echo "Error: --force-recreate and --update are mutually exclusive"
  exit 1
fi

if [[ -z "$MODE" ]]; then
  echo "Error: one of --model-runs, --processed, --sync-models, or --register-model is required"
  exit 1
fi

if [[ "$MODE" == "register-model" ]]; then
  if [[ ${#IMAGES[@]} -eq 0 ]]; then
    echo "Error: --register-model requires at least one docker image URI"
    exit 1
  fi
  for image in "${IMAGES[@]}"; do
python -c "
import os
from tools.ckan_tools import ensure_model
from tools.lakefs_tools import ensure_model_fdo

image = '$image'
if ':' in image.split('/')[-1]:
    docker_image, docker_tag = image.rsplit(':', 1)
else:
    docker_image, docker_tag = image, ''
model_name = docker_image.split('/')[-1]
lakefs_models_repo = os.environ.get('LAKEFS_MODELS_REPO', 'models')
force = $FORCE == True

print(f'Writing model FDO to lakeFS: {model_name}')
model_qid, model_fdo_bytes = ensure_model_fdo(
    docker_image=docker_image,
    model_name=model_name,
    docker_tag=docker_tag,
    lakefs_models_repo=lakefs_models_repo,
    force=force,
)
print(f'  QID: {model_qid}')
print(f'Registering in CKAN: {model_name}')
ensure_model(model_name=model_name, docker_image=docker_image, docker_tag=docker_tag, model_qid=model_qid, fdo_bytes=model_fdo_bytes, force_recreate=force)
print(f'Done: {model_name}')
"
  done
elif [[ "$MODE" == "sync-models" ]]; then
python -c "
import os
from tools.lakefs_tools import list_models
from flow.sync_ckan_with_lakefs_models import _do_sync_model

repo = os.environ.get('LAKEFS_MODELS_REPO', 'models')
force_recreate = $FORCE
for model_qid in list_models(repo):
    _do_sync_model(model_qid, repo, force_recreate=force_recreate)
"
elif [[ "$MODE" == "data-processed" ]]; then
python -c "
from tools.lakefs_tools import list_raw_datasets
from flow.sync_ckan_with_lakefs_dataprocessed import _do_sync_raw_dataset

repo = 'data-processed'
force_recreate = $FORCE
update = $UPDATE
for fdo_path in list_raw_datasets(repo):
    _do_sync_raw_dataset(fdo_path, repo, force_recreate=force_recreate, update=update)
"
else
python -c "
from tools.lakefs_tools import list_runs
from flow.sync_ckan_with_lakefs_modelruns import _do_sync_run

repo = 'model-runs'
force_recreate = $FORCE
for run_id in list_runs(repo):
    _do_sync_run(run_id, repo, force_recreate=force_recreate)
"
fi
