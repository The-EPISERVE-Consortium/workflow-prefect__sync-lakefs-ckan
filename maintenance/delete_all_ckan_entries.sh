#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

TYPE_FILTER=""
DELETE_ALL=0

if [[ $# -eq 0 ]]; then
    echo "Usage: $0 --all | --type TYPE  (e.g. --type model-run)" >&2
    exit 1
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --all)
            DELETE_ALL=1
            shift
            ;;
        --type)
            TYPE_FILTER="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: $0 --all | --type TYPE  (e.g. --type model-run)" >&2
            exit 1
            ;;
    esac
done

if [[ $DELETE_ALL -eq 0 && -z "$TYPE_FILTER" ]]; then
    echo "Error: specify --all or --type TYPE" >&2
    exit 1
fi

export CKAN_API_TOKEN=$(kubectl get secret ckan-credentials -o jsonpath='{.data.ckan-api-token}' | base64 -d)
export CKAN_HOST=https://data.episerve.zib.de
export TYPE_FILTER
export DELETE_ALL

python3 -c "
import os, sys, requests

ckan_host   = os.environ['CKAN_HOST']
headers     = {'Authorization': os.environ['CKAN_API_TOKEN']}
type_filter = os.environ.get('TYPE_FILTER', '')
delete_all  = os.environ.get('DELETE_ALL') == '1'

if type_filter:
    packages = []
    rows, start = 1000, 0
    while True:
        r = requests.get(
            f'{ckan_host}/api/3/action/package_search',
            headers=headers,
            params={'fq': f'groups:type-{type_filter}', 'rows': rows, 'start': start},
        )
        result = r.json()['result']
        packages.extend(p['name'] for p in result['results'])
        start += rows
        if start >= result['count']:
            break
    scope = f'group=type-{type_filter}'
elif delete_all:
    r        = requests.get(f'{ckan_host}/api/3/action/package_list', headers=headers)
    packages = r.json()['result']
    scope    = 'ALL types'
else:
    print('Error: no scope specified.', file=sys.stderr)
    sys.exit(1)

if not packages:
    print(f'No packages found in CKAN ({scope}).')
    sys.exit(0)

print(f'About to delete {len(packages)} packages ({scope}) from {ckan_host}:')
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
