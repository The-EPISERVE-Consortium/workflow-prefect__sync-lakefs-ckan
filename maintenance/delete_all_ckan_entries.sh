#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

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
