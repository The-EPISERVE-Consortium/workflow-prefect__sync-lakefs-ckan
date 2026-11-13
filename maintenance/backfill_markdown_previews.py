"""Add raw-text views to existing Markdown resources using the sync credentials.

From the repository root, run:
    python -m maintenance.backfill_markdown_previews          # dry run
    python -m maintenance.backfill_markdown_previews --apply  # create missing views

Uses the same CKAN_HOST and CKAN_API_TOKEN environment as the sync flows.
"""

import argparse
import json

from tools.ckan_tools import backfill_markdown_previews


def main() -> None:
    """Report missing Markdown views and apply changes only with an explicit flag."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Create missing text views')
    args = parser.parse_args()
    resources = backfill_markdown_previews(apply=args.apply)
    print(json.dumps({'applied': args.apply, 'count': len(resources), 'resources': resources}, indent=2))


if __name__ == '__main__':
    main()
