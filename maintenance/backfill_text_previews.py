"""Backfill raw-text previews for MD, TXT, XML, CONF, YAML and YML resources.

From the repository root, run:
    python -m maintenance.backfill_text_previews
    python -m maintenance.backfill_text_previews --apply

Uses the same CKAN_HOST and CKAN_API_TOKEN environment as the sync flows.
"""

import argparse
import json

from tools.ckan_tools import backfill_text_previews


def main() -> None:
    """Report candidates and create missing views only with the apply flag."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Create missing text views')
    args = parser.parse_args()
    resources = backfill_text_previews(apply=args.apply)
    print(json.dumps({'applied': args.apply, 'count': len(resources), 'resources': resources}, indent=2))


if __name__ == '__main__':
    main()
