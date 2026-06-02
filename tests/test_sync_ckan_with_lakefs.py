"""Unit tests for sync_ckan_with_lakefs."""

import os
from unittest.mock import MagicMock, patch

import pytest

import flow.sync_ckan_with_lakefs as m
from tools.ckan_tools import (
    _ckan_run_exists,
    create_model,
    create_model_run,
    ensure_model,
)
from tools.lakefs_tools import get_run_metadata
from tools.sharding import shard_qid

# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def required_env(monkeypatch):
    monkeypatch.setenv("CKAN_API_TOKEN",    "test-ckan-token")
    monkeypatch.setenv("LAKEFS_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("LAKEFS_SECRET_KEY", "test-secret-key")


def _vocab_get_response():
    mock = MagicMock()
    mock.json.return_value = {
        "result": [
            {"name": "domain",   "id": "v-domain"},
            {"name": "modality", "id": "v-modality"},
            {"name": "status",   "id": "v-status"},
        ]
    }
    return mock


def _post_response(result):
    mock = MagicMock()
    mock.json.return_value = {"success": True, "result": result}
    return mock


# ── create_model ───────────────────────────────────────────────────────────────

class TestCreateModel:
    def test_idempotent_returns_existing_without_package_create(self):
        existing = {"id": "abc", "name": "my-model"}

        get_mock = MagicMock()
        get_mock.json.return_value = {"success": True, "result": existing}

        with patch("requests.get", return_value=get_mock) as mock_get, \
             patch("requests.post") as mock_post:
            result = create_model(
                name="my-model",
                description="A CT segmentation model.",
                git_repo="https://github.com/example/ct-seg",
                docker_image="ghcr.io/example/ct-seg:1.0",
                algorithm="u-net",
                input_format="nifti",
                output_format="nifti",
                lead_researcher="dr. smith",
                domain="imaging",
                modality="ct-scan",
            )

        assert result == existing
        mock_post.assert_not_called()
        mock_get.assert_called_once()

    def test_creates_dataset_when_not_found(self):
        not_found_mock = MagicMock()
        not_found_mock.json.return_value = {"success": False, "error": {"message": "Not found"}}

        new_pkg = {"id": "new-id", "name": "my-model"}

        def get_side_effect(url, **_):
            if "package_show" in url:
                return not_found_mock
            return _vocab_get_response()

        with patch("requests.get", side_effect=get_side_effect), \
             patch("requests.post", return_value=_post_response(new_pkg)):
            result = create_model(
                name="my-model",
                description="desc",
                git_repo="https://github.com/example/model",
                docker_image="img:1.0",
                algorithm="algo",
                input_format="nifti",
                output_format="nifti",
                lead_researcher="dr. jones",
                domain="imaging",
                modality="ct-scan",
            )

        assert result == new_pkg


# ── ensure_model ──────────────────────────────────────────────────────────────

class TestEnsureModel:
    def test_creates_placeholder_when_not_found(self):
        not_found_mock = MagicMock()
        not_found_mock.json.return_value = {"success": False, "error": {"message": "Not found"}}
        new_pkg = {"id": "new-id", "name": "ct-seg"}

        def get_side_effect(url, **_):
            if "package_show" in url:
                return not_found_mock
            return _vocab_get_response()

        with patch("requests.get", side_effect=get_side_effect), \
             patch("requests.post", return_value=_post_response(new_pkg)) as mock_post:
            result = ensure_model("ct-seg", docker_tag="2.1.0")

        assert result == new_pkg
        payload = mock_post.call_args[1]["json"]
        assert payload["name"] == "ct-seg"
        assert payload["extras"][1] == {"key": "docker_image", "value": "2.1.0"}

    def test_returns_existing_without_creating(self):
        existing = {"id": "abc", "name": "ct-seg"}
        get_mock = MagicMock()
        get_mock.json.return_value = {"success": True, "result": existing}

        with patch("requests.get", return_value=get_mock), \
             patch("requests.post") as mock_post:
            result = ensure_model("ct-seg")

        assert result == existing
        mock_post.assert_not_called()


# ── create_model_run ───────────────────────────────────────────────────────────

_BASE = "http://test-lakefs/api/v1/repositories/model-runs/refs/main/objects"


class TestCreateModelRun:
    def test_calls_package_create_once_and_resource_create_per_file(self):
        pkg = {"id": "pkg-123", "name": "run-2026-001"}
        input_files  = [f"{_BASE}?path=run-2026-001%2Finput%2Fconfig.yaml&presign=false"]
        output_files = [f"{_BASE}?path=run-2026-001%2Foutput%2Fresult.nii&presign=false"]

        mock_pkg_resp = _post_response(pkg)
        mock_res_resp = _post_response({})

        rocrate_bytes = b'{"@context": "test"}'

        with patch("requests.get", return_value=_vocab_get_response()), \
             patch("requests.post") as mock_post:
            mock_post.side_effect = [mock_pkg_resp, mock_res_resp, mock_res_resp, mock_res_resp]

            result = create_model_run(
                model_name       = "ct-seg",
                run_id           = "run-2026-001",
                qid              = "Q1748526042817",
                docker_tag       = "2.1.0",
                run_timestamp    = "2026-05-15T11:00:00Z",
                status           = "success",
                computation_time = "",
                rocrate_bytes    = rocrate_bytes,
                input_files      = input_files,
                output_files     = output_files,
            )

        # 1 package_create + 1 rocrate + 1 input resource + 1 output resource
        assert mock_post.call_count == 4
        assert result == pkg

    def test_creates_one_resource_per_input_and_output(self):
        pkg = {"id": "pkg-456", "name": "run-multi"}

        with patch("requests.get", return_value=_vocab_get_response()), \
             patch("requests.post") as mock_post:
            mock_post.side_effect = [
                _post_response(pkg),
                _post_response({}),  # rocrate
                _post_response({}),  # input 1
                _post_response({}),  # input 2
                _post_response({}),  # output 1
            ]

            create_model_run(
                model_name       = "ct-seg",
                run_id           = "run-multi",
                qid              = "Q1748526042817",
                docker_tag       = "1.0",
                run_timestamp    = "2026-05-15T11:00:00Z",
                status           = "success",
                computation_time = "",
                rocrate_bytes    = b'{"@context": "test"}',
                input_files      = [
                    f"{_BASE}?path=run-multi%2Finput%2Fa.yaml&presign=false",
                    f"{_BASE}?path=run-multi%2Finput%2Fb.yaml&presign=false",
                ],
                output_files     = [f"{_BASE}?path=run-multi%2Foutput%2Fout.nii&presign=false"],
            )

        assert mock_post.call_count == 5  # 1 pkg + 1 rocrate + 2 inputs + 1 output


# ── _ckan_run_exists ───────────────────────────────────────────────────────────

class TestCkanRunExists:
    def test_returns_true_when_count_gt_0(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": {"count": 1, "results": [{"id": "abc"}]}}

        with patch("requests.get", return_value=mock_resp):
            assert _ckan_run_exists("run-2026-001") is True

    def test_returns_false_when_count_is_0(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": {"count": 0, "results": []}}

        with patch("requests.get", return_value=mock_resp):
            assert _ckan_run_exists("run-2026-001") is False


# ── sync_run task ──────────────────────────────────────────────────────────────

class TestSyncRun:
    def test_skips_run_when_already_in_ckan(self):
        with patch.object(m, "_ckan_run_exists", return_value=True) as mock_exists, \
             patch.object(m, "create_model_run") as mock_create:
            m._do_sync_run("run-2026-001", "model-runs")

        mock_exists.assert_called_once_with("run-2026-001")
        mock_create.assert_not_called()

    def test_creates_run_when_not_in_ckan(self):
        input_files   = [f"{_BASE}?path=run-001%2Finput%2Fconfig.yaml&presign=false"]
        output_files  = [f"{_BASE}?path=run-001%2Foutput%2Fresult.nii&presign=false"]
        rocrate_bytes = b'{"@context": "test"}'
        metadata = {
            "model_name":       "ct-seg",
            "qid":              "Q1748526042817",
            "docker_tag":       "2.1.0",
            "run_timestamp":    "2026-05-15T11:00:00Z",
            "status":           "success",
            "computation_time": "",
            "rocrate_bytes":    rocrate_bytes,
            "input_files":      input_files,
            "output_files":     output_files,
        }

        with patch.object(m, "_ckan_run_exists", return_value=False), \
             patch.object(m, "get_run_metadata", return_value=metadata), \
             patch.object(m, "ensure_model") as mock_ensure, \
             patch.object(m, "create_model_run") as mock_create:
            m._do_sync_run("run-001", "model-runs")

        mock_ensure.assert_called_once_with(model_name="ct-seg", docker_tag="2.1.0")
        mock_create.assert_called_once_with(
            model_name       = "ct-seg",
            run_id           = "run-001",
            qid              = "Q1748526042817",
            docker_tag       = "2.1.0",
            run_timestamp    = "2026-05-15T11:00:00Z",
            status           = "success",
            computation_time = "",
            rocrate_bytes    = rocrate_bytes,
            input_files      = input_files,
            output_files     = output_files,
        )


    def test_skips_ensure_model_when_model_name_empty(self):
        metadata = {
            "model_name": "", "qid": "", "docker_tag": "",
            "run_timestamp": "", "status": "", "computation_time": "",
            "rocrate_bytes": b"", "input_files": [], "output_files": [],
        }
        with patch.object(m, "_ckan_run_exists", return_value=False), \
             patch.object(m, "get_run_metadata", return_value=metadata), \
             patch.object(m, "ensure_model") as mock_ensure, \
             patch.object(m, "create_model_run"):
            m._do_sync_run("run-001", "model-runs")

        mock_ensure.assert_not_called()


# ── get_run_metadata ───────────────────────────────────────────────────────────

_LAKEFS_BASE = "http://test-lakefs/api/v1/repositories/model-runs/refs/main/objects"
_QID         = "Q1748526042817"
# shard_qid("Q1748526042817") → "17/48/52/Q1748526042817"

# New-format: relative component paths in @id
_INPUT_REL  = "components/input/config.yaml"
_OUTPUT_REL = "components/output/result.nii"
# DOIP retrieve URLs stored as CKAN resource URLs
_INPUT_URL  = "http://test-doip/doip/retrieve/Q1748526042817/input/config.yaml"
_OUTPUT_URL = "http://test-doip/doip/retrieve/Q1748526042817/output/result.nii"

_RO_CRATE = {
    "@context": "https://w3id.org/ro/crate/1.1/context",
    "@graph": [
        {
            "@id": "ro-crate-metadata.json",
            "@type": "CreativeWork",
            "conformsTo": [
                {"@id": "https://w3id.org/ro/crate/1.1"},
                {"@id": "https://w3id.org/ro/wfrun/process/0.4"},
            ],
            "about": {"@id": "./"},
        },
        {
            "@id": "./",
            "@type": "Dataset",
            "name":          "ct-seg",
            "description":   "Model run of ct-seg",
            "datePublished": "2026-05-15T11:00:00Z",
            "license":       "unknown",
            "identifier":    "Q1748526042817",
            "hasPart":       [{"@id": _INPUT_REL}, {"@id": _OUTPUT_REL}],
            "mentions":      [{"@id": "#run"}],
        },
        {
            "@id":          "#run",
            "@type":        "CreateAction",
            "instrument":   {"@id": "#ct-seg"},
            "object":       [{"@id": _INPUT_REL}],
            "result":       [{"@id": _OUTPUT_REL}],
            "startTime":    "2026-05-15T11:00:00Z",
            "endTime":      "2026-05-15T11:00:42Z",
            "actionStatus": {"@id": "https://schema.org/CompletedActionStatus"},
        },
        {
            "@id":             "#ct-seg",
            "@type":           "SoftwareApplication",
            "name":            "ct-seg",
            "softwareVersion": "2.1.0",
            "url":             "ghcr.io/example/ct-seg",
        },
        {"@id": _INPUT_REL,  "@type": "File", "name": "config.yaml", "encodingFormat": "application/yaml"},
        {"@id": _OUTPUT_REL, "@type": "File", "name": "result.nii"},
    ],
}

# Legacy-format: full lakeFS API URLs in @id (used for backward-compat tests)
_LEGACY_INPUT_URL  = f"{_LAKEFS_BASE}?path=17%2F48%2F52%2FQ1748526042817%2Finput%2Fconfig.yaml&presign=false"
_LEGACY_OUTPUT_URL = f"{_LAKEFS_BASE}?path=17%2F48%2F52%2FQ1748526042817%2Foutput%2Fresult.nii&presign=false"


class TestGetRunMetadata:
    def _mock_object(self, crate: dict):
        import json
        reader = MagicMock()
        reader.__enter__ = lambda s: s
        reader.__exit__ = MagicMock(return_value=False)
        reader.read.return_value = json.dumps(crate).encode()
        obj = MagicMock()
        obj.reader.return_value = reader
        return obj

    def test_extracts_all_fields(self):
        obj = self._mock_object(_RO_CRATE)
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            mock_repo.return_value.branch.return_value.object.return_value = obj
            result = get_run_metadata(_QID, "model-runs")

        import json as _json
        assert result["model_name"]       == "ct-seg"
        assert result["qid"]              == "Q1748526042817"
        assert result["docker_tag"]       == "2.1.0"
        assert result["run_timestamp"]    == "2026-05-15T11:00:00Z"
        assert result["status"]           == "success"
        assert result["computation_time"] == 42
        assert result["input_files"]      == [_INPUT_URL]
        assert result["output_files"]     == [_OUTPUT_URL]
        assert _json.loads(result["rocrate_bytes"]) == _RO_CRATE

    def test_reads_correct_object_path(self):
        obj = self._mock_object(_RO_CRATE)
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            branch_mock = mock_repo.return_value.branch.return_value
            branch_mock.object.return_value = obj
            get_run_metadata(_QID, "model-runs")

        branch_mock.object.assert_called_once_with(
            f"{shard_qid(_QID)}/ro-crate-metadata.json"
        )

    def test_missing_optional_fields_default_to_empty_string(self):
        sparse_crate = {
            "@context": "https://w3id.org/ro/crate/1.1/context",
            "@graph": [
                {
                    "@id": "ro-crate-metadata.json", "@type": "CreativeWork",
                    "conformsTo": [
                        {"@id": "https://w3id.org/ro/crate/1.1"},
                        {"@id": "https://w3id.org/ro/wfrun/process/0.4"},
                    ],
                    "about": {"@id": "./"},
                },
                {"@id": "./", "@type": "Dataset", "name": "my-model"},
            ],
        }
        obj = self._mock_object(sparse_crate)
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            mock_repo.return_value.branch.return_value.object.return_value = obj
            result = get_run_metadata(_QID, "model-runs")

        assert result["model_name"]       == "my-model"
        assert result["qid"]              == ""
        assert result["docker_tag"]       == ""
        assert result["run_timestamp"]    == ""
        assert result["status"]           == ""
        assert result["computation_time"] == ""
        assert result["rocrate_bytes"]    != b""
        assert result["input_files"]      == []
        assert result["output_files"]     == []

    def test_identifier_used_as_qid_fallback(self):
        crate = {
            "@context": "https://w3id.org/ro/crate/1.1/context",
            "@graph": [
                {
                    "@id": "ro-crate-metadata.json", "@type": "CreativeWork",
                    "conformsTo": [
                        {"@id": "https://w3id.org/ro/crate/1.1"},
                        {"@id": "https://w3id.org/ro/wfrun/process/0.4"},
                    ],
                    "about": {"@id": "./"},
                },
                {"@id": "./", "@type": "Dataset", "name": "my-model", "identifier": "Q9999"},
            ],
        }
        obj = self._mock_object(crate)
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            mock_repo.return_value.branch.return_value.object.return_value = obj
            result = get_run_metadata("Q9999", "model-runs")

        assert result["qid"] == "Q9999"

    def test_legacy_full_url_hasPart_classified_correctly(self):
        """Old-style full lakeFS API URLs in hasPart are still routed to input/output lists."""
        legacy_crate = {
            "@context": "https://w3id.org/ro/crate/1.1/context",
            "@graph": [
                {
                    "@id": "ro-crate-metadata.json", "@type": "CreativeWork",
                    "conformsTo": [
                        {"@id": "https://w3id.org/ro/crate/1.1"},
                        {"@id": "https://w3id.org/ro/wfrun/process/0.4"},
                    ],
                    "about": {"@id": "./"},
                },
                {
                    "@id": "./",
                    "@type": "Dataset",
                    "name": "ct-seg",
                    "hasPart": [{"@id": _LEGACY_INPUT_URL}, {"@id": _LEGACY_OUTPUT_URL}],
                },
            ],
        }
        obj = self._mock_object(legacy_crate)
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            mock_repo.return_value.branch.return_value.object.return_value = obj
            result = get_run_metadata(_QID, "model-runs")

        assert result["input_files"]  == [_LEGACY_INPUT_URL]
        assert result["output_files"] == [_LEGACY_OUTPUT_URL]

    def test_raises_on_missing_graph(self):
        obj = self._mock_object({"@context": "https://w3id.org/ro/crate/1.1/context"})
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            mock_repo.return_value.branch.return_value.object.return_value = obj
            with pytest.raises(ValueError, match="no @graph"):
                get_run_metadata(_QID, "model-runs")

    def test_raises_on_missing_root_dataset(self):
        obj = self._mock_object({
            "@context": "https://w3id.org/ro/crate/1.1/context",
            "@graph": [
                {"@id": "ro-crate-metadata.json", "@type": "CreativeWork"},
            ],
        })
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            mock_repo.return_value.branch.return_value.object.return_value = obj
            with pytest.raises(ValueError, match="no root Dataset entry"):
                get_run_metadata(_QID, "model-runs")
