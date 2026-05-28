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

python -c "
from lakefs.exceptions import ObjectNotFoundException
from flow.tools.lakefs_tools import list_runs, get_run_metadata, list_run_files
from flow.tools.ckan_tools import _ckan_run_exists, create_model_run

repo = 'model-runs'
for run_id in list_runs(repo):
    if _ckan_run_exists(run_id):
        print(f'{run_id}: already in CKAN')
        continue
    try:
        meta = get_run_metadata(run_id, repo)
    except ObjectNotFoundException:
        print(f'{run_id}: no metadata.json, using empty defaults')
        meta = {
            'model_name': '', 'git_commit': '', 'docker_tag': '',
            'run_timestamp': '', 'status': '', 'domain': '', 'modality': '',
        }
    print(f'{run_id}: syncing...')
    create_model_run(
        model_name=meta['model_name'], run_id=run_id,
        git_commit=meta['git_commit'], docker_tag=meta['docker_tag'],
        run_timestamp=meta['run_timestamp'], status=meta['status'],
        domain=meta['domain'], modality=meta['modality'],
        input_files=list_run_files(run_id, 'input', repo),
        output_files=list_run_files(run_id, 'output', repo),
    )
    print(f'{run_id}: done')
"
