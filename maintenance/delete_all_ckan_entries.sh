#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

usage() {
  echo "Usage: $0 [--delete] [--sync-data] [--sync-models] [--sync-runs] [--sync-all] [--force-recreate]"
  echo ""
  echo "  --delete          Delete all packages from CKAN (requires confirmation)"
  echo "  --sync-data       Sync data-processed lakeFS repo → CKAN"
  echo "  --sync-models     Sync model FDOs from lakeFS models repo → CKAN"
  echo "  --sync-runs       Sync model-runs lakeFS repo → CKAN"
  echo "  --sync-all        Run --sync-data, --sync-models, and --sync-runs"
  echo "  --force-recreate  Pass --force-recreate to sync operations"
  echo ""
  echo "Flags may be combined, e.g. --delete --sync-all to wipe and repopulate."
  exit 0
}

DO_DELETE=false
DO_SYNC_DATA=false
DO_SYNC_MODELS=false
DO_SYNC_RUNS=false
FORCE_ARG=

if [[ $# -eq 0 ]]; then
  usage
fi

for arg in "$@"; do
  case "$arg" in
    --delete)         DO_DELETE=true ;;
    --sync-data)      DO_SYNC_DATA=true ;;
    --sync-models)    DO_SYNC_MODELS=true ;;
    --sync-runs)      DO_SYNC_RUNS=true ;;
    --sync-all)       DO_SYNC_DATA=true; DO_SYNC_MODELS=true; DO_SYNC_RUNS=true ;;
    --force-recreate) FORCE_ARG=--force-recreate ;;
    --help|-h)        usage ;;
    *)                echo "Unknown argument: $arg"; exit 1 ;;
  esac
done

if [[ "$DO_DELETE" == "true" ]]; then
  export CKAN_API_TOKEN=$(kubectl get secret ckan-credentials -o jsonpath='{.data.ckan-api-token}' | base64 -d)
  export CKAN_HOST=https://data.episerve.zib.de

  python3 -c "
import os, sys, requests

ckan_host = os.environ['CKAN_HOST']
headers   = {'Authorization': os.environ['CKAN_API_TOKEN']}

r        = requests.get(f'{ckan_host}/api/3/action/package_list', headers=headers)
packages = r.json()['result']

if not packages:
    print('No packages found in CKAN.')
    sys.exit(0)

print(f'About to delete {len(packages)} packages from {ckan_host}:')
for name in packages:
    print(f'  {name}')

print()
answer = input('Type YES to confirm: ')
if answer != 'YES':
    print('Aborted.')
    sys.exit(1)

print()
errors = []
for name in packages:
    resp = requests.post(
        f'{ckan_host}/api/3/action/package_delete',
        headers=headers,
        json={'id': name},
    )
    if resp.json().get('success'):
        print(f'  deleted: {name}')
    else:
        print(f'  FAILED:  {name} — {resp.json().get(\"error\")}')
        errors.append(name)

print()
if errors:
    print(f'Done with {len(errors)} error(s): {errors}')
    sys.exit(1)
else:
    print(f'Done. Deleted {len(packages)} packages.')
"
fi

if [[ "$DO_SYNC_DATA" == "true" ]]; then
  echo "==> Syncing data-processed → CKAN"
  ./run_locally.sh --sync-data $FORCE_ARG
fi

if [[ "$DO_SYNC_MODELS" == "true" ]]; then
  echo "==> Syncing models → CKAN"
  ./run_locally.sh --sync-models $FORCE_ARG
fi

if [[ "$DO_SYNC_RUNS" == "true" ]]; then
  echo "==> Syncing model-runs → CKAN"
  ./run_locally.sh --sync-model-runs $FORCE_ARG
fi
