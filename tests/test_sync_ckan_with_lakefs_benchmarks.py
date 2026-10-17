"""Unit tests for sync_ckan_with_lakefs_benchmarks."""

from unittest.mock import patch

import pytest

import flow.sync_ckan_with_lakefs_benchmarks as m

_PROFILE = {"episerveBenchmark": {"id": 3, "role": "input", "source_qids": ["Q1", "Q2"],
                                  "data_commit": "c" * 40, "sha256": "a" * 64}}
_META = {"qid": "Q1791000000001", "name": "Flu (input data)", "description": "d", "source_url": "",
         "modified": "2026-10-17T00:00:00Z", "components": [{"filename": "input.parquet", "url": "u", "media_type": "x"}],
         "fdo_bytes": b"{}", "additional_type": "benchmarks", "access": "open", "profile": _PROFILE}


@pytest.fixture(autouse=True)
def required_env(monkeypatch):
    for key in ("CKAN_API_TOKEN", "LAKEFS_ACCESS_KEY", "LAKEFS_SECRET_KEY"):
        monkeypatch.setenv(key, "x")
    monkeypatch.setenv("LAKEFS_HOST", "http://test-lakefs")
    monkeypatch.setenv("CKAN_HOST", "http://test-ckan")


def test_extras_describe_the_benchmark_object():
    extras = {e["key"]: e["value"] for e in m.benchmark_extras(_PROFILE)}
    assert extras == {"benchmark_id": "3", "benchmark_role": "input", "source_qids": "Q1,Q2",
                      "data_commit": "c" * 40, "sha256": "a" * 64}
    assert m.benchmark_extras({}) == []


def test_a_new_object_is_created_in_its_own_group_and_stays_open():
    with patch.object(m, "get_raw_dataset_metadata", return_value=_META), \
         patch.object(m, "_ckan_raw_dataset_exists", return_value=False), \
         patch.object(m, "create_raw_dataset") as create:
        m._do_sync_benchmark_object("p/Q1791000000001.fdo.json", "benchmarks", log=lambda *_: None)
    kwargs = create.call_args.kwargs
    assert kwargs["group"] == "type-benchmark-data" and kwargs["dataset_type"] == "benchmark-data"
    assert kwargs["access"] == "open" and kwargs["qid"] == "Q1791000000001"
    assert {"key": "benchmark_id", "value": "3"} in kwargs["extra_extras"]


def test_an_object_that_is_already_in_ckan_or_unreadable_is_skipped():
    with patch.object(m, "get_raw_dataset_metadata", return_value=_META), \
         patch.object(m, "_ckan_raw_dataset_exists", return_value=True), \
         patch.object(m, "create_raw_dataset") as create:
        m._do_sync_benchmark_object("p", "benchmarks", log=lambda *_: None)
    with patch.object(m, "get_raw_dataset_metadata", side_effect=ValueError("bad")), \
         patch.object(m, "create_raw_dataset") as create2:
        m._do_sync_benchmark_object("p", "benchmarks", log=lambda *_: None)
    assert not create.called and not create2.called
