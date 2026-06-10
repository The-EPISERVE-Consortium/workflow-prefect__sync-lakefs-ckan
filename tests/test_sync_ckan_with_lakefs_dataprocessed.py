"""Unit tests for sync_ckan_with_lakefs_dataprocessed."""

import json
from unittest.mock import MagicMock, patch

import pytest

import flow.sync_ckan_with_lakefs_dataprocessed as m
from tools.ckan_tools import (
    _ckan_add_preview_view,
    _ckan_fetch_raw_dataset,
    _ckan_raw_dataset_exists,
    create_raw_dataset,
    update_raw_dataset,
)
from tools.lakefs_tools import get_raw_dataset_metadata, list_raw_datasets

# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def required_env(monkeypatch):
    monkeypatch.setenv("CKAN_API_TOKEN",    "test-ckan-token")
    monkeypatch.setenv("LAKEFS_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("LAKEFS_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("LAKEFS_HOST",       "http://test-lakefs")
    monkeypatch.setenv("CKAN_HOST",         "http://test-ckan")


_QID      = "Q1780428359320"
_FDO_PATH = "incidence/covid/RKI__covid_germany.fdo.json"
_FDO_DATA_URL = "http://test-doip/doip/retrieve/Q1780428359320/RKI__covid_germany.csv"

_FDO = {
    "@context": ["https://w3id.org/fdo/context/v1"],
    "@id": _QID,
    "@type": "DigitalObject",
    "kernel": {
        "@id": _QID,
        "digitalObjectType": "https://schema.org/Dataset",
        "primaryIdentifier": _QID,
        "kernelVersion": "v1",
        "immutable": False,
        "modified": "2026-06-02T19:25:59Z",
        "fdo:hasComponent": [
            {
                "@id": "#RKI__covid_germany.csv",
                "componentId": "RKI__covid_germany.csv",
                "mediaType": "text/csv",
            }
        ],
    },
    "profile": {
        "@context": "https://schema.org/",
        "@type": "Dataset",
        "@id": _QID,
        "name": "corona_incidence_germany",
        "description": "Dataset corona_incidence_germany downloaded from https://example.com",
        "url": "https://example.com/data.csv",
    },
    "provenance": {
        "@id": "#run",
        "@type": "prov:Activity",
        "prov:startedAtTime": "2026-06-02T19:00:00Z",
        "prov:endedAtTime": "2026-06-02T19:25:59Z",
        "prov:wasAssociatedWith": {
            "@id": "EPISERVE Consortium dataset downloader",
            "@type": "prov:SoftwareAgent",
        },
        "prov:used": [],
    },
}


def _mock_object(data: dict):
    reader = MagicMock()
    reader.__enter__ = lambda s: s
    reader.__exit__ = MagicMock(return_value=False)
    reader.read.return_value = json.dumps(data).encode()
    obj = MagicMock()
    obj.reader.return_value = reader
    return obj


def _post_response(result):
    mock = MagicMock()
    mock.json.return_value = {"success": True, "result": result}
    return mock


# ── get_raw_dataset_metadata ───────────────────────────────────────────────────

class TestGetRawDatasetMetadata:
    def test_extracts_all_fields(self):
        obj = _mock_object(_FDO)
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            mock_repo.return_value.branch.return_value.object.return_value = obj
            result = get_raw_dataset_metadata(_FDO_PATH, "data-raw")

        assert result["qid"]         == _QID
        assert result["name"]        == "corona_incidence_germany"
        assert result["description"] == "Dataset corona_incidence_germany downloaded from https://example.com"
        assert result["source_url"]  == "https://example.com/data.csv"
        assert result["modified"]    == "2026-06-02T19:25:59Z"
        assert result["fdo_bytes"]   == json.dumps(_FDO).encode()
        assert result["components"]  == [
            {"filename": "RKI__covid_germany.csv", "url": _FDO_DATA_URL, "media_type": "text/csv"}
        ]

    def test_uses_display_name_when_available(self):
        fdo = {**_FDO, "profile": {**_FDO["profile"], "display_name": "COVID-19 Germany Incidence"}}
        obj = _mock_object(fdo)
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            mock_repo.return_value.branch.return_value.object.return_value = obj
            result = get_raw_dataset_metadata(_FDO_PATH, "data-raw")

        assert result["name"] == "COVID-19 Germany Incidence"

    def test_reads_correct_object_path(self):
        obj = _mock_object(_FDO)
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            branch_mock = mock_repo.return_value.branch.return_value
            branch_mock.object.return_value = obj
            get_raw_dataset_metadata(_FDO_PATH, "data-raw")

        branch_mock.object.assert_called_once_with(_FDO_PATH)

    def test_component_url_uses_doip(self):
        obj = _mock_object(_FDO)
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            mock_repo.return_value.branch.return_value.object.return_value = obj
            result = get_raw_dataset_metadata(_FDO_PATH, "data-raw")

        assert result["components"][0]["url"] == _FDO_DATA_URL

    def test_empty_components_when_none_in_kernel(self):
        fdo = {**_FDO, "kernel": {**_FDO["kernel"], "fdo:hasComponent": []}}
        obj = _mock_object(fdo)
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            mock_repo.return_value.branch.return_value.object.return_value = obj
            result = get_raw_dataset_metadata(_FDO_PATH, "data-raw")

        assert result["components"] == []

    def test_missing_optional_fields_default_to_empty(self):
        sparse_fdo = {"@id": _QID, "@type": "DigitalObject", "kernel": {}, "profile": {}, "provenance": {}}
        obj = _mock_object(sparse_fdo)
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            mock_repo.return_value.branch.return_value.object.return_value = obj
            result = get_raw_dataset_metadata(_FDO_PATH, "data-raw")

        assert result["qid"]         == _QID
        assert result["name"]        == ""
        assert result["description"] == ""
        assert result["source_url"]  == ""
        assert result["modified"]    == ""
        assert result["components"]  == []


# ── list_raw_datasets ──────────────────────────────────────────────────────────

class TestListRawDatasets:
    def test_returns_only_fdo_json_paths(self):
        entries = [
            MagicMock(path="incidence/covid/RKI__covid_germany.fdo.json"),
            MagicMock(path="incidence/covid/RKI__covid_germany.csv"),
            MagicMock(path="wastewater/RKI__wastewater.tsv"),
            MagicMock(path="incidence/influenza/RKI__grippeweb.fdo.json"),
        ]
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            mock_repo.return_value.branch.return_value.objects.return_value = entries
            result = list_raw_datasets("data-raw")

        assert result == [
            "incidence/covid/RKI__covid_germany.fdo.json",
            "incidence/influenza/RKI__grippeweb.fdo.json",
        ]


# ── _ckan_raw_dataset_exists ───────────────────────────────────────────────────

class TestCkanRawDatasetExists:
    def test_returns_true_when_found(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": {"count": 1, "results": [{"id": "abc"}]}}
        with patch("requests.get", return_value=mock_resp):
            assert _ckan_raw_dataset_exists(_QID) is True

    def test_returns_false_when_not_found(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": {"count": 0, "results": []}}
        with patch("requests.get", return_value=mock_resp):
            assert _ckan_raw_dataset_exists(_QID) is False


# ── create_raw_dataset ─────────────────────────────────────────────────────────

class TestCkanAddPreviewView:
    def test_log_resource_gets_text_view(self):
        with patch("tools.ckan_tools._ckan_api") as mock_api:
            _ckan_add_preview_view({"id": "resource-1", "format": "LOG"})

        mock_api.assert_called_once_with(
            "resource_view_create",
            {
                "resource_id": "resource-1",
                "view_type":   "text_view",
                "title":       "Preview",
            },
        )


class TestCreateRawDataset:
    def test_calls_package_create_then_fdo_upload_then_resource_per_component(self):
        pkg = {"id": "pkg-raw-1", "name": _QID.lower()}
        components = [
            {"filename": "data.csv", "url": "http://lakefs/data.csv", "media_type": "text/csv"},
            {"filename": "data.tsv", "url": "http://lakefs/data.tsv", "media_type": "text/tab-separated-values"},
        ]

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [
                _post_response(pkg),   # package_create
                _post_response({}),    # fdo upload (resource_create multipart)
                _post_response({}),    # resource data.csv
                _post_response({}),    # resource data.tsv
            ]
            result = create_raw_dataset(
                qid         = _QID,
                name        = "corona_incidence_germany",
                description = "desc",
                source_url  = "https://example.com",
                modified    = "2026-06-02T19:25:59Z",
                components  = components,
                fdo_bytes   = b'{"@id": "Q1"}',
            )

        assert mock_post.call_count == 4
        assert result == pkg

    def test_no_fdo_upload_when_fdo_bytes_empty(self):
        pkg = {"id": "pkg-raw-2", "name": _QID.lower()}
        with patch("requests.post") as mock_post:
            mock_post.side_effect = [_post_response(pkg)]
            create_raw_dataset(
                qid="Q999", name="n", description="d", source_url="",
                modified="", components=[], fdo_bytes=b"",
            )

        assert mock_post.call_count == 1  # only package_create

    def test_package_create_payload(self):
        pkg = {"id": "pkg-raw-3", "name": _QID.lower()}
        with patch("requests.post") as mock_post:
            mock_post.side_effect = [_post_response(pkg)]
            create_raw_dataset(
                qid="Q42", name="my-dataset", description="desc",
                source_url="https://src.example.com", modified="2026-01-01T00:00:00Z",
                components=[], fdo_bytes=b"",
            )

        payload = mock_post.call_args[1]["json"]
        assert payload["name"]                      == "q42"
        assert payload["title"]                     == "my-dataset"
        assert payload["groups"]                    == [{"name": "type-raw-data"}]
        assert {"key": "qid",      "value": "Q42"}  in payload["extras"]
        assert {"key": "modified", "value": "2026-01-01T00:00:00Z"} in payload["extras"]


# ── _do_sync_raw_dataset ───────────────────────────────────────────────────────

_METADATA = {
    "qid":         _QID,
    "name":        "corona_incidence_germany",
    "description": "desc",
    "source_url":  "https://example.com",
    "modified":    "2026-06-02T19:25:59Z",
    "components":  [{"filename": "data.csv", "url": "http://lakefs/data.csv", "media_type": "text/csv"}],
    "fdo_bytes":   b'{"@id": "Q1"}',
}


class TestDoSyncRawDataset:
    def test_skips_when_already_in_ckan(self):
        with patch.object(m, "_ckan_raw_dataset_exists", return_value=True), \
             patch.object(m, "get_raw_dataset_metadata", return_value=_METADATA), \
             patch.object(m, "create_raw_dataset") as mock_create:
            m._do_sync_raw_dataset(_FDO_PATH, "data-raw")

        mock_create.assert_not_called()

    def test_creates_when_not_in_ckan(self):
        with patch.object(m, "_ckan_raw_dataset_exists", return_value=False), \
             patch.object(m, "get_raw_dataset_metadata", return_value=_METADATA), \
             patch.object(m, "create_raw_dataset") as mock_create:
            m._do_sync_raw_dataset(_FDO_PATH, "data-raw")

        mock_create.assert_called_once_with(
            qid         = _QID,
            name        = "corona_incidence_germany",
            description = "desc",
            source_url  = "https://example.com",
            modified    = "2026-06-02T19:25:59Z",
            components  = _METADATA["components"],
            fdo_bytes   = b'{"@id": "Q1"}',
            additional_type = "",
        )

    def test_force_recreate_deletes_then_creates(self):
        with patch.object(m, "_ckan_raw_dataset_exists", return_value=True), \
             patch.object(m, "get_raw_dataset_metadata", return_value=_METADATA), \
             patch.object(m, "_ckan_delete_raw_dataset") as mock_delete, \
             patch.object(m, "create_raw_dataset") as mock_create:
            m._do_sync_raw_dataset(_FDO_PATH, "data-raw", force_recreate=True)

        mock_delete.assert_called_once_with(_QID)
        mock_create.assert_called_once()

    def test_skips_when_metadata_raises(self):
        with patch.object(m, "get_raw_dataset_metadata", side_effect=ValueError("bad json")), \
             patch.object(m, "create_raw_dataset") as mock_create:
            m._do_sync_raw_dataset(_FDO_PATH, "data-raw")

        mock_create.assert_not_called()

    def test_skips_when_qid_empty(self):
        metadata = {**_METADATA, "qid": ""}
        with patch.object(m, "get_raw_dataset_metadata", return_value=metadata), \
             patch.object(m, "create_raw_dataset") as mock_create:
            m._do_sync_raw_dataset(_FDO_PATH, "data-raw")

        mock_create.assert_not_called()

    def test_update_calls_update_raw_dataset_when_exists(self):
        with patch.object(m, "_ckan_raw_dataset_exists", return_value=True), \
             patch.object(m, "get_raw_dataset_metadata", return_value=_METADATA), \
             patch.object(m, "update_raw_dataset", return_value={"modified": ("", "2026-06-02T19:25:59Z")}) as mock_update, \
             patch.object(m, "create_raw_dataset") as mock_create:
            m._do_sync_raw_dataset(_FDO_PATH, "data-raw", update=True)

        mock_update.assert_called_once_with(
            qid         = _QID,
            name        = "corona_incidence_germany",
            description = "desc",
            source_url  = "https://example.com",
            modified    = "2026-06-02T19:25:59Z",
            additional_type = "",
        )
        mock_create.assert_not_called()

    def test_update_no_changes_does_not_call_create(self):
        with patch.object(m, "_ckan_raw_dataset_exists", return_value=True), \
             patch.object(m, "get_raw_dataset_metadata", return_value=_METADATA), \
             patch.object(m, "update_raw_dataset", return_value={}), \
             patch.object(m, "create_raw_dataset") as mock_create:
            m._do_sync_raw_dataset(_FDO_PATH, "data-raw", update=True)

        mock_create.assert_not_called()

    def test_update_falls_through_to_create_when_not_in_ckan(self):
        with patch.object(m, "_ckan_raw_dataset_exists", return_value=False), \
             patch.object(m, "get_raw_dataset_metadata", return_value=_METADATA), \
             patch.object(m, "update_raw_dataset") as mock_update, \
             patch.object(m, "create_raw_dataset") as mock_create:
            m._do_sync_raw_dataset(_FDO_PATH, "data-raw", update=True)

        mock_update.assert_not_called()
        mock_create.assert_called_once()

    def test_force_recreate_and_update_raises(self):
        with patch.object(m, "get_raw_dataset_metadata", return_value=_METADATA):
            with pytest.raises(ValueError, match="mutually exclusive"):
                m._do_sync_raw_dataset(_FDO_PATH, "data-raw", force_recreate=True, update=True)


# ── _ckan_fetch_raw_dataset ────────────────────────────────────────────────────

_CKAN_PKG = {
    "id":    "pkg-uuid-123",
    "title": "corona_incidence_germany",
    "notes": "desc",
    "url":   "https://example.com",
    "extras": [
        {"key": "dataset_type", "value": "raw-data"},
        {"key": "qid",          "value": _QID},
        {"key": "modified",     "value": ""},
    ],
}


class TestCkanFetchRawDataset:
    def test_returns_package_when_found(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": {"count": 1, "results": [_CKAN_PKG]}}
        with patch("requests.get", return_value=mock_resp):
            result = _ckan_fetch_raw_dataset(_QID)

        assert result == _CKAN_PKG

    def test_returns_none_when_not_found(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": {"count": 0, "results": []}}
        with patch("requests.get", return_value=mock_resp):
            result = _ckan_fetch_raw_dataset(_QID)

        assert result is None


# ── update_raw_dataset ─────────────────────────────────────────────────────────

class TestUpdateRawDataset:
    def _get_resp(self, pkg=_CKAN_PKG):
        mock = MagicMock()
        mock.json.return_value = {"result": {"count": 1, "results": [pkg]}}
        return mock

    def test_patches_when_modified_differs(self):
        with patch("requests.get", return_value=self._get_resp()), \
             patch("requests.post") as mock_post:
            mock_post.return_value = _post_response({})
            result = update_raw_dataset(
                qid=_QID, name="corona_incidence_germany", description="desc",
                source_url="https://example.com", modified="2026-06-02T19:25:59Z",
            )

        assert result == {"modified": ("", "2026-06-02T19:25:59Z")}
        payload = mock_post.call_args[1]["json"]
        assert payload["id"] == "pkg-uuid-123"
        extras_by_key = {e["key"]: e["value"] for e in payload["extras"]}
        assert extras_by_key["modified"] == "2026-06-02T19:25:59Z"

    def test_no_patch_when_nothing_changed(self):
        up_to_date_pkg = {
            **_CKAN_PKG,
            "extras": [
                {"key": "dataset_type", "value": "raw-data"},
                {"key": "qid",          "value": _QID},
                {"key": "modified",     "value": "2026-06-02T19:25:59Z"},
            ],
        }
        with patch("requests.get", return_value=self._get_resp(up_to_date_pkg)), \
             patch("requests.post") as mock_post:
            result = update_raw_dataset(
                qid=_QID, name="corona_incidence_germany", description="desc",
                source_url="https://example.com", modified="2026-06-02T19:25:59Z",
            )

        assert result == {}
        mock_post.assert_not_called()

    def test_patches_only_top_level_when_extras_match(self):
        pkg_with_old_title = {
            **_CKAN_PKG,
            "title": "old-title",
            "extras": [
                {"key": "dataset_type", "value": "raw-data"},
                {"key": "qid",          "value": _QID},
                {"key": "modified",     "value": "2026-06-02T19:25:59Z"},
            ],
        }
        with patch("requests.get", return_value=self._get_resp(pkg_with_old_title)), \
             patch("requests.post") as mock_post:
            mock_post.return_value = _post_response({})
            update_raw_dataset(
                qid=_QID, name="corona_incidence_germany", description="desc",
                source_url="https://example.com", modified="2026-06-02T19:25:59Z",
            )

        payload = mock_post.call_args[1]["json"]
        assert payload["title"] == "corona_incidence_germany"
        assert "extras" not in payload

    def test_extras_patch_sends_all_three_keys(self):
        with patch("requests.get", return_value=self._get_resp()), \
             patch("requests.post") as mock_post:
            mock_post.return_value = _post_response({})
            update_raw_dataset(
                qid=_QID, name="corona_incidence_germany", description="desc",
                source_url="https://example.com", modified="2026-06-02T19:25:59Z",
            )

        payload = mock_post.call_args[1]["json"]
        keys = {e["key"] for e in payload["extras"]}
        assert keys == {"dataset_type", "qid", "modified", "additional_type"}
