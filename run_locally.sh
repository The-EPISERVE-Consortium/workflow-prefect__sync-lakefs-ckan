#!/bin/bash

# Extract secrets from the cluster
export CKAN_API_TOKEN=$(kubectl get secret ckan-credentials -o jsonpath='{.data.ckan-api-token}' | base64 -d)
export LAKEFS_ACCESS_KEY=$(kubectl get secret lakefs-credentials -o jsonpath='{.data.lakefs-access-key}' | base64 -d)
export LAKEFS_SECRET_KEY=$(kubectl get secret lakefs-credentials -o jsonpath='{.data.lakefs-secret-key}' | base64 -d)

# Set the host vars
export CKAN_HOST=https://data.episerve.zib.de
export LAKEFS_HOST=https://lake-episerve.zib.de/
export DOIP_HOST=https://doip.episerve.zib.de

# Run
if [ ! -d .venv ]; then
    echo "Creating virtual environment..."
    python -m venv .venv
    .venv/bin/pip install -q -r requirements.txt
fi

source .venv/bin/activate

if [[ $# -eq 0 ]]; then
  echo "Usage: run_locally.sh --model-runs|--processed|--models <image> [<image> ...] [--force-recreate] [--update]"
  echo ""
  echo "  --model-runs      Sync the model-runs repo"
  echo "  --processed       Sync the data-processed repo"
  echo "  --models          Register one or more model placeholders in CKAN by docker image URI"
  echo "  --force-recreate  Overwrite datasets that already exist in CKAN"
  echo "  --update          Patch changed fields for datasets already in CKAN (mutually exclusive with --force-recreate)"
  echo ""
  echo "Examples:"
  echo "  run_locally.sh --model-runs"
  echo "  run_locally.sh --processed --force-recreate"
  echo "  run_locally.sh --processed --update"
  echo "  run_locally.sh --models ghcr.io/the-episerve-consortium/model__prediction__grippeweb__baseline-nullmodel:latest"
  exit 0
fi

FORCE=False
UPDATE=False
MODE=
IMAGES=()

for arg in "$@"; do
  case "$arg" in
    --force-recreate) FORCE=True ;;
    --update)         UPDATE=True ;;
    --model-runs)     MODE=model-runs ;;
    --processed)      MODE=data-processed ;;
    --models)         MODE=models ;;
    *)
      if [[ "$MODE" == "models" ]]; then
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
  echo "Error: one of --model-runs, --processed, or --models is required"
  exit 1
fi

if [[ "$MODE" == "models" ]]; then
  if [[ ${#IMAGES[@]} -eq 0 ]]; then
    echo "Error: --models requires at least one docker image URI"
    exit 1
  fi
  for image in "${IMAGES[@]}"; do
python -c "
from tools.ckan_tools import ensure_model

image = '$image'
if ':' in image.split('/')[-1]:
    docker_image, docker_tag = image.rsplit(':', 1)
else:
    docker_image, docker_tag = image, ''
model_name = docker_image.split('/')[-1]
print(f'Registering model: {model_name}')
ensure_model(model_name=model_name, docker_image=docker_image, docker_tag=docker_tag)
print(f'Done: {model_name}')
"
  done
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
