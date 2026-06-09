#!/bin/bash
# Deletes a specific path (and all its contents) from the main branch of a lakeFS repository.
# The parent directory and its other contents are preserved.
#
# Usage: delete_lakefs_path.sh <repo-name> <path>
# Requires: lakectl
#
# Example: delete_lakefs_path.sh model-runs 17/81/00/Q1781003251339

set -euo pipefail

if [ -z "${1:-}" ] || [ -z "${2:-}" ]; then
    echo "Usage: $0 <repo-name> <path>"
    echo ""
    echo "Deletes all files under <path> from the main branch of the given lakeFS repository."
    echo "The parent directory and its other contents are preserved."
    echo ""
    echo "Example: $0 model-runs 17/81/00/Q1781003251339"
    exit 1
fi

REPO="$1"
# Strip any leading/trailing slashes from the path argument
PATH_ARG="${2#/}"
PATH_ARG="${PATH_ARG%/}"
BRANCH="main"

echo "This will permanently delete all files under lakefs://${REPO}/${BRANCH}/${PATH_ARG}/."
echo "The parent directory and its other contents will be preserved."
echo ""
read -r -p "Type YES to continue: " confirm
if [ "$confirm" != "YES" ]; then
    echo "Aborted."
    exit 1
fi

echo "Deleting lakefs://${REPO}/${BRANCH}/${PATH_ARG}/ ..."

if ! lakectl fs rm -r "lakefs://${REPO}/${BRANCH}/${PATH_ARG}/"; then
    echo "Path lakefs://${REPO}/${BRANCH}/${PATH_ARG}/ is already empty or does not exist, nothing to do."
    exit 0
fi

lakectl commit "lakefs://${REPO}/${BRANCH}" -m "maintenance: delete path ${PATH_ARG}"
echo "Done. Deleted lakefs://${REPO}/${BRANCH}/${PATH_ARG}/."
