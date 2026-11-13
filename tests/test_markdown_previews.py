"""Verify raw Markdown view registration and idempotent existing-resource updates."""

from unittest.mock import patch

import pytest

from tools.ckan_tools import RAW_TEXT_FORMATS, _ckan_add_preview_view, backfill_markdown_previews, backfill_text_previews


@pytest.mark.parametrize('format', ['MD', 'md', 'MARKDOWN', 'markdown', 'TXT', 'txt', 'XML', 'xml', 'CONF', 'conf', 'YAML', 'yaml', 'YML', 'yml'])
def test_markdown_uses_existing_text_view(format):
    """Register plain-text views for the supported Markdown format aliases.

    Args:
        format: Markdown format spelling returned by CKAN.
    """
    with patch('tools.ckan_tools._ckan_api') as api:
        _ckan_add_preview_view({'id': 'notes', 'format': format})
    api.assert_called_once_with('resource_view_create', {
        'resource_id': 'notes', 'view_type': 'text_view', 'title': 'Preview'})


def test_backfill_dry_run_and_repeat_do_not_duplicate_views():
    """Report candidates without writes, then add each missing view exactly once."""
    views = {'old': [{'view_type': 'text_view'}], 'notes': []}
    created = []

    def api(action, payload):
        """Simulate paginated catalog reads and record view creation.

        Args:
            action: CKAN action name.
            payload: Action parameters.

        Returns:
            Action result matching CKAN's resource search or view APIs.
        """
        if action == 'resource_search':
            if payload['query'] == 'format:MARKDOWN':
                return {'count': 0, 'results': []}
            resources = [{'id': 'old', 'format': 'MD'}, {'id': 'notes', 'name': 'notes.md', 'format': 'MD'}]
            offset = payload['offset']
            return {'count': 2, 'results': resources[offset:offset + 1]}
        if action == 'resource_view_list':
            return views[payload['id']]
        assert action == 'resource_view_create'
        created.append(payload)
        views[payload['resource_id']].append({'view_type': payload['view_type']})
        return {}

    with patch('tools.ckan_tools._ckan_api', side_effect=api):
        assert backfill_markdown_previews() == [{'id': 'notes', 'name': 'notes.md'}]
        assert not created
        assert backfill_markdown_previews(apply=True) == [{'id': 'notes', 'name': 'notes.md'}]
        assert backfill_markdown_previews(apply=True) == []
    assert created == [{'resource_id': 'notes', 'view_type': 'text_view', 'title': 'Preview'}]



def test_backfill_covers_every_raw_text_format_without_recreating_existing_views():
    """Inspect all supported formats and create only the missing view for each."""
    created = set()

    def api(action, payload):
        """Simulate resources and persisted views for each supported format.

        Args:
            action: CKAN API action.
            payload: Action arguments.

        Returns:
            Matching fake resource, view, or creation result.
        """
        if action == 'resource_search':
            fmt = payload['query'].split(':')[1]
            return {'count': 1, 'results': [{'id': fmt, 'format': fmt, 'name': 'file.' + fmt.lower()}]}
        if action == 'resource_view_list':
            return [{'view_type': 'text_view'}] if payload['id'] in created else []
        assert action == 'resource_view_create'
        assert payload['view_type'] == 'text_view'
        created.add(payload['resource_id'])
        return {}

    with patch('tools.ckan_tools._ckan_api', side_effect=api):
        assert len(backfill_text_previews()) == len(RAW_TEXT_FORMATS)
        assert not created
        assert len(backfill_text_previews(apply=True)) == len(RAW_TEXT_FORMATS)
        assert backfill_text_previews(apply=True) == []
    assert created == set(RAW_TEXT_FORMATS)
