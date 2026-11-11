"""Portable benchmark definitions are discoverable, pinned, and synchronized in place."""

import json
from unittest.mock import MagicMock, patch

import pytest

from tools import ckan_tools, lakefs_tools
import flow.sync_ckan_with_lakefs_benchmarks as flow

COMMIT = "a" * 64
CHECKSUM = "b" * 64
COMPONENT = {"filename": "benchmark-definition.json", "url": f"https://doip.test/doip/retrieve/Q123/benchmark-definition.json?version={COMMIT}",
             "media_type": "application/json", "sha256": CHECKSUM}


def test_definition_added_without_replacing_data_resources_or_package():
    """Add one JSON resource and its preview while retaining original resource IDs."""
    pkg = {"id": "package-id", "resources": [{"id": "input-id", "name": "input.parquet"},
                                               {"id": "reference-id", "name": "reference.parquet"}]}
    with patch.object(ckan_tools, "_ckan_fetch_raw_dataset", return_value=pkg), \
         patch.object(ckan_tools, "_ckan_api", return_value={"id": "definition-id", "format": "JSON"}) as api:
        assert ckan_tools.sync_benchmark_definition("Q123", [COMPONENT])
    assert [call.args[0] for call in api.call_args_list] == ["resource_create", "resource_view_create"]
    resource = api.call_args_list[0].args[1]
    assert resource["package_id"] == "package-id" and resource["hash"] == CHECKSUM
    assert resource["url"] == COMPONENT["url"] and resource["name"] == "benchmark-definition.json"
    assert resource["description"].startswith("Benchmark definition")
    assert pkg["resources"][0]["id"] == "input-id"


def test_repeated_sync_keeps_the_existing_pin_when_content_is_identical():
    """An unrelated lakeFS commit must not churn the descriptor URL or its views."""
    with patch.object(ckan_tools, "_ckan_api", return_value={"id": "definition-id", "format": "JSON"}) as api:
        ckan_tools.sync_benchmark_definition("Q123", [COMPONENT], package={"id": "p", "resources": []})
    current = {"id": "definition-id", **api.call_args_list[0].args[1]}
    newer = {**COMPONENT, "url": COMPONENT["url"].replace(COMMIT, "c" * 64)}
    with patch.object(ckan_tools, "_ckan_api") as api:
        assert not ckan_tools.sync_benchmark_definition("Q123", [newer], package={"id": "p", "resources": [current]})
    api.assert_not_called()


def test_changed_definition_is_patched_in_place_without_recreating_views():
    """Retain the resource ID when its definition content receives a new version."""
    pkg = {"id": "p", "resources": [{"id": "definition-id", "name": "benchmark-definition.json", "hash": "c" * 64,
                                       "url": COMPONENT["url"].replace(COMMIT, "d" * 64)}]}
    with patch.object(ckan_tools, "_ckan_api") as api:
        assert ckan_tools.sync_benchmark_definition("Q123", [COMPONENT], package=pkg)
    api.assert_called_once()
    action, fields = api.call_args.args
    assert action == "resource_patch" and fields["id"] == "definition-id"
    assert fields["hash"] == CHECKSUM and fields["url"] == COMPONENT["url"]


def test_definition_without_a_frozen_url_is_rejected():
    """Avoid publishing a mutable link for recovery metadata."""
    with patch.object(ckan_tools, "_ckan_api") as api, pytest.raises(ValueError, match="immutable"):
        ckan_tools.sync_benchmark_definition("Q123", [{**COMPONENT, "url": COMPONENT["url"].split("?")[0]}])
    api.assert_not_called()


def test_component_metadata_and_links_use_the_same_repository_snapshot(monkeypatch):
    """Read the FDO at a captured commit even if the branch moves afterward.

    Args:
        monkeypatch: Fixture providing the public DOIP origin.
    """
    monkeypatch.setenv("DOIP_PUBLIC_URL", "https://doip.test")
    fdo = {"@id": "Q123", "kernel": {"fdo:hasComponent": [
        {"componentId": "benchmark-definition.json", "mediaType": "application/json"}]}}
    repo = MagicMock()
    repo.branch.return_value.head.id = COMMIT
    repo.ref.return_value.object.return_value.reader.return_value.__enter__.return_value.read.return_value = json.dumps(fdo).encode()
    with patch.object(lakefs_tools, "_lakefs_client"), patch.object(lakefs_tools.lakefs, "Repository", return_value=repo):
        result = lakefs_tools.get_raw_dataset_metadata("path/Q123.fdo.json", "benchmarks", pin_components=True)
    repo.ref.assert_called_once_with(COMMIT)
    repo.branch.return_value.object.assert_not_called()
    assert result["components"][0]["url"] == COMPONENT["url"]


def test_existing_benchmark_is_reconciled_using_its_fdo_definition_checksum():
    """Update catalog discovery for objects that already existed before definitions."""
    metadata = {"qid": "Q123", "components": [{key: value for key, value in COMPONENT.items() if key != "sha256"}],
                "profile": {"episerveBenchmark": {"files": {"definition": {"sha256": CHECKSUM}}}}}
    with patch.object(flow, "get_raw_dataset_metadata", return_value=metadata) as read, \
         patch.object(flow, "_ckan_raw_dataset_exists", return_value=True), \
         patch.object(flow, "sync_benchmark_definition", return_value=True) as reconcile, \
         patch.object(flow, "create_raw_dataset") as create:
        flow._do_sync_benchmark_object("path/Q123.fdo.json", "benchmarks", log=lambda *_: None)
    read.assert_called_once_with("path/Q123.fdo.json", "benchmarks", pin_components=True)
    reconcile.assert_called_once_with("Q123", [COMPONENT])
    create.assert_not_called()


def test_new_benchmark_definition_is_created_once_after_its_data_resources():
    """Keep the descriptor out of generic resource creation before reconciliation."""
    metadata = {"qid": "Q123", "name": "Benchmark", "description": "", "source_url": "", "modified": "", "fdo_bytes": b"{}",
                "components": [{"filename": "input.parquet", "url": "input-url"}, COMPONENT],
                "profile": {"episerveBenchmark": {"files": {"definition": {"sha256": CHECKSUM}}}}}
    pkg = {"id": "p", "resources": []}
    with patch.object(flow, "get_raw_dataset_metadata", return_value=metadata), \
         patch.object(flow, "_ckan_raw_dataset_exists", return_value=False), \
         patch.object(flow, "create_raw_dataset", return_value=pkg) as create, \
         patch.object(flow, "sync_benchmark_definition") as reconcile:
        flow._do_sync_benchmark_object("path", "benchmarks", log=lambda *_: None)
    assert [component["filename"] for component in create.call_args.kwargs["components"]] == ["input.parquet"]
    assert reconcile.call_args.kwargs["package"] is pkg
