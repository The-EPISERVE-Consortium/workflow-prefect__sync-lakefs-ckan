"""Unit tests for sync_ckan_with_lakefs_modelruns."""

import os
from unittest.mock import MagicMock, patch

import pytest

import flow.sync_ckan_with_lakefs_modelruns as m
from tools.ckan_tools import (
    _ckan_run_exists,
    create_model,
    create_model_run,
    ensure_model,
)
from tools.lakefs_tools import ensure_model_fdo, get_run_metadata
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
        existing = {"id": "abc", "name": "q0000000000001", "title": "my-model", "notes": "A CT segmentation model.", "url": "https://github.com/example/ct-seg"}

        get_mock = MagicMock()
        get_mock.json.return_value = {"success": True, "result": existing}

        with patch("requests.get", return_value=get_mock) as mock_get, \
             patch("requests.post") as mock_post:
            result = create_model(
                name="my-model",
                description="A CT segmentation model.",
                git_repo="https://github.com/example/ct-seg",
                docker_image="ghcr.io/example/ct-seg",
                docker_tag="1.0",
                algorithm="u-net",
                input_format="nifti",
                output_format="nifti",
                lead_researcher="dr. smith",
                domain="imaging",
                modality="ct-scan",
                model_qid="Q0000000000001",
            )

        assert result == existing
        mock_post.assert_not_called()
        mock_get.assert_called_once()

    def test_force_recreate_deletes_then_creates(self):
        existing = {"id": "abc", "name": "q0000000000001"}

        get_mock = MagicMock()
        get_mock.json.return_value = {"success": True, "result": existing}

        new_pkg = {"id": "new-abc", "name": "q0000000000001"}

        def get_side_effect(url, **_):
            if "package_show" in url:
                return get_mock
            return _vocab_get_response()

        with patch("requests.get", side_effect=get_side_effect), \
             patch("requests.post") as mock_post:
            mock_post.side_effect = [
                _post_response({}),      # package_delete
                _post_response(new_pkg), # package_create
            ]
            result = create_model(
                name="my-model", description="desc",
                git_repo="", docker_image="", docker_tag="",
                algorithm="", input_format="", output_format="",
                lead_researcher="", model_qid="Q0000000000001", force_recreate=True,
            )

        assert result == new_pkg
        delete_call, create_call = mock_post.call_args_list
        assert "package_delete" in delete_call[0][0]
        assert "package_create" in create_call[0][0]

    def test_creates_dataset_when_not_found(self):
        not_found_mock = MagicMock()
        not_found_mock.json.return_value = {"success": False, "error": {"message": "Not found"}}

        new_pkg = {"id": "new-id", "name": "q0000000000001"}

        def get_side_effect(url, **_):
            if "package_show" in url:
                return not_found_mock
            return _vocab_get_response()

        with patch("requests.get", side_effect=get_side_effect), \
             patch("requests.post", return_value=_post_response(new_pkg)) as mock_post:
            result = create_model(
                name="my-model",
                description="desc",
                git_repo="https://github.com/example/model",
                docker_image="ghcr.io/example/model",
                docker_tag="1.0",
                algorithm="algo",
                input_format="nifti",
                output_format="nifti",
                lead_researcher="dr. jones",
                domain="imaging",
                modality="ct-scan",
                model_qid="Q0000000000001",
            )

        assert result == new_pkg
        payload = mock_post.call_args[1]["json"]
        assert payload["name"]  == "q0000000000001"
        assert payload["title"] == "my-model"

    def test_patches_placeholder_description_when_real_one_provided(self):
        existing = {"id": "abc", "name": "q0000000000001", "notes": "Auto-created placeholder for model 'my-model'.\n\n### [Browse all runs →](http://ckan/dataset?q=extras_model_qid:Q0000000000001)", "url": ""}
        patched  = {**existing, "notes": "Real description.\n\n### [Browse all runs →](http://ckan/dataset?q=extras_model_qid:Q0000000000001)", "url": "https://github.com/example/model"}

        get_mock = MagicMock()
        get_mock.json.return_value = {"success": True, "result": existing}

        def get_side(url, **_):
            if "package_show" in url:
                return get_mock
            return _vocab_get_response()

        with patch("requests.get", side_effect=get_side), \
             patch("requests.post") as mock_post:
            mock_post.return_value = _post_response(patched)
            result = create_model(
                name="my-model", description="Real description.",
                git_repo="https://github.com/example/model",
                docker_image="", docker_tag="", algorithm="",
                input_format="", output_format="", lead_researcher="",
                model_qid="Q0000000000001",
            )

        assert "package_patch" in mock_post.call_args[0][0]
        payload = mock_post.call_args[1]["json"]
        assert "Real description." in payload["notes"]
        assert payload["url"] == "https://github.com/example/model"
        assert result == patched

    def test_no_patch_when_description_already_real(self):
        existing = {"id": "abc", "name": "q0000000000001", "notes": "Already a real description.", "url": ""}

        get_mock = MagicMock()
        get_mock.json.return_value = {"success": True, "result": existing}

        with patch("requests.get", return_value=get_mock), \
             patch("requests.post") as mock_post:
            result = create_model(
                name="my-model", description="New description.",
                git_repo="", docker_image="", docker_tag="", algorithm="",
                input_format="", output_format="", lead_researcher="",
                model_qid="Q0000000000001",
            )

        mock_post.assert_not_called()
        assert result == existing


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
            result = ensure_model("ct-seg", docker_image="ghcr.io/example/ct-seg", docker_tag="2.1.0", model_qid="Q0000000000001")

        assert result == new_pkg
        payload = mock_post.call_args[1]["json"]
        assert payload["name"]  == "q0000000000001"
        assert payload["title"] == "ct-seg"
        extras = {e["key"]: e["value"] for e in payload["extras"]}
        assert extras["docker_image"] == "ghcr.io/example/ct-seg"

    def test_returns_existing_without_creating(self):
        existing = {"id": "abc", "name": "ct-seg"}
        get_mock = MagicMock()
        get_mock.json.return_value = {"success": True, "result": existing}

        with patch("requests.get", return_value=get_mock), \
             patch("requests.post") as mock_post:
            result = ensure_model("ct-seg", model_qid="Q0000000000001")

        assert result == existing
        mock_post.assert_not_called()

    def test_stores_docker_image_created_in_extras(self):
        not_found_mock = MagicMock()
        not_found_mock.json.return_value = {"success": False, "error": {"message": "Not found"}}
        new_pkg = {"id": "new-id", "name": "mymodel"}

        def get_side_effect(url, **_):
            if "package_show" in url:
                return not_found_mock
            return _vocab_get_response()

        with patch("tools.ckan_tools.get_image_created", return_value="2025-11-14T09:32:17Z"), \
             patch("requests.get", side_effect=get_side_effect), \
             patch("requests.post", return_value=_post_response(new_pkg)) as mock_post:
            ensure_model("mymodel", docker_image="ghcr.io/org/mymodel", docker_tag="1.0", model_qid="Q0000000000001")

        extras = {e["key"]: e["value"] for e in mock_post.call_args[1]["json"]["extras"]}
        assert extras["docker_image_created"] == "2025-11-14T09:32:17Z"


# ── create_model_run ───────────────────────────────────────────────────────────

_BASE = "http://test-lakefs/api/v1/repositories/model-runs/refs/main/objects"


class TestCreateModelRun:
    def test_calls_package_create_once_and_resource_create_per_file(self):
        pkg = {"id": "pkg-123", "name": "run-2026-001"}
        input_files  = [f"{_BASE}?path=run-2026-001%2Finput%2Fconfig.yaml&presign=false"]
        output_files = [f"{_BASE}?path=run-2026-001%2Foutput%2Fresult.nii&presign=false"]

        mock_pkg_resp = _post_response(pkg)
        mock_res_resp = _post_response({})

        fdo_bytes     = b'{"@id": "Q1748526042817"}'
        rocrate_bytes = b'{"@context": "rocrate"}'

        with patch("requests.get", return_value=_vocab_get_response()), \
             patch("requests.post") as mock_post:
            mock_post.side_effect = [mock_pkg_resp, mock_res_resp, mock_res_resp, mock_res_resp, mock_res_resp]

            result = create_model_run(
                model_name       = "ct-seg",
                run_id           = "run-2026-001",
                qid              = "Q1748526042817",
                docker_tag       = "2.1.0",
                run_timestamp    = "2026-05-15T11:00:00Z",
                status           = "success",
                computation_time = "",
                fdo_bytes        = fdo_bytes,
                rocrate_bytes    = rocrate_bytes,
                input_files      = input_files,
                output_files     = output_files,
            )

        # 1 package_create + 1 fdo upload + 1 rocrate upload + 1 input + 1 output
        assert mock_post.call_count == 5
        assert result == pkg

    def test_creates_one_resource_per_input_and_output(self):
        pkg = {"id": "pkg-456", "name": "run-multi"}

        with patch("requests.get", return_value=_vocab_get_response()), \
             patch("requests.post") as mock_post:
            mock_post.side_effect = [
                _post_response(pkg),
                _post_response({}),  # fdo upload
                _post_response({}),  # rocrate upload
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
                fdo_bytes        = b'{"@id": "Q1748526042817"}',
                rocrate_bytes    = b'{"@context": "rocrate"}',
                input_files      = [
                    f"{_BASE}?path=run-multi%2Finput%2Fa.yaml&presign=false",
                    f"{_BASE}?path=run-multi%2Finput%2Fb.yaml&presign=false",
                ],
                output_files     = [f"{_BASE}?path=run-multi%2Foutput%2Fout.nii&presign=false"],
            )

        assert mock_post.call_count == 6  # 1 pkg + 1 fdo upload + 1 rocrate upload + 2 inputs + 1 output


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
        input_files = [f"{_BASE}?path=run-001%2Finput%2Fconfig.yaml&presign=false"]
        output_files = [f"{_BASE}?path=run-001%2Foutput%2Fresult.nii&presign=false"]
        fdo_bytes     = b'{"@id": "Q1748526042817"}'
        rocrate_bytes = b'{"@context": "rocrate"}'
        # model_image is present so ensure_model_fdo will be called
        metadata = {
            "model_name":       "ct-seg",
            "model_image":      "ghcr.io/example/ct-seg",
            "qid":              "Q1748526042817",
            "docker_tag":       "2.1.0",
            "run_timestamp":    "2026-05-15T11:00:42Z",
            "status":           "",
            "computation_time": "",
            "fdo_bytes":        fdo_bytes,
            "rocrate_bytes":    rocrate_bytes,
            "input_files":      input_files,
            "output_files":     output_files,
        }
        fake_model_qid  = "Q0000000000001"
        fake_fdo_bytes  = b'{"@id": "Q0000000000001"}'

        with patch.object(m, "_ckan_run_exists", return_value=False), \
             patch.object(m, "get_run_metadata", return_value=metadata), \
             patch.object(m, "ensure_model_fdo", return_value=(fake_model_qid, fake_fdo_bytes)) as mock_fdo, \
             patch.object(m, "ensure_model") as mock_ensure, \
             patch.object(m, "create_model_run") as mock_create:
            m._do_sync_run("run-001", "model-runs")

        mock_fdo.assert_called_once_with(
            docker_image       = "ghcr.io/example/ct-seg",
            model_name         = "ct-seg",
            docker_tag         = "2.1.0",
            lakefs_models_repo = "models",
            force              = False,
            log                = print,
        )
        mock_ensure.assert_called_once_with(
            model_name="ct-seg", docker_image="ghcr.io/example/ct-seg",
            docker_tag="2.1.0", model_qid=fake_model_qid, fdo_bytes=fake_fdo_bytes,
            description="", git_repo="", additional_properties=[], force_recreate=False,
        )
        mock_create.assert_called_once_with(
            model_name       = "ct-seg",
            run_id           = "run-001",
            qid              = "Q1748526042817",
            docker_tag       = "2.1.0",
            run_timestamp    = "2026-05-15T11:00:42Z",
            status           = "",
            computation_time = "",
            fdo_bytes        = fdo_bytes,
            rocrate_bytes    = rocrate_bytes,
            input_files      = input_files,
            output_files     = output_files,
            model_qid        = fake_model_qid,
            input_dataset_qids = [],
            data_transformation_sql = [],
        )


    def test_skips_ensure_model_when_model_name_empty(self):
        metadata = {
            "model_name": "", "model_image": "", "qid": "", "docker_tag": "",
            "run_timestamp": "", "status": "", "computation_time": "",
            "fdo_bytes": b"", "rocrate_bytes": b"", "input_files": [], "output_files": [],
        }
        with patch.object(m, "_ckan_run_exists", return_value=False), \
             patch.object(m, "get_run_metadata", return_value=metadata), \
             patch.object(m, "ensure_model_fdo") as mock_fdo, \
             patch.object(m, "ensure_model") as mock_ensure, \
             patch.object(m, "create_model_run"):
            m._do_sync_run("run-001", "model-runs")

        mock_fdo.assert_not_called()
        mock_ensure.assert_not_called()


# ── get_run_metadata ───────────────────────────────────────────────────────────

_LAKEFS_BASE = "http://test-lakefs/api/v1/repositories/model-runs/refs/main/objects"
_QID         = "Q1748526042817"
# shard_qid("Q1748526042817") → "17/48/52/Q1748526042817"

_INPUT_REL  = "components/input/config.yaml"
_OUTPUT_REL = "components/output/result.nii"
_INPUT_URL  = "http://test-doip/doip/retrieve/Q1748526042817/input/config.yaml"
_OUTPUT_URL = "http://test-doip/doip/retrieve/Q1748526042817/output/result.nii"

_FDO = {
    "@context": [
        "https://w3id.org/fdo/context/v1",
        {"schema": "https://schema.org/", "prov": "http://www.w3.org/ns/prov#", "fdo": "https://w3id.org/fdo/vocabulary/"},
    ],
    "@id":   _QID,
    "@type": "DigitalObject",
    "kernel": {
        "@id":               _QID,
        "digitalObjectType": "https://schema.org/Dataset",
        "primaryIdentifier": _QID,
        "kernelVersion":     "v1",
        "immutable":         False,
        "modified":          "2026-05-15T11:00:42Z",
        "fdo:hasComponent": [
            {"@id": _INPUT_REL,  "componentId": "config.yaml", "mediaType": "application/yaml"},
            {"@id": _OUTPUT_REL, "componentId": "result.nii",  "mediaType": "application/octet-stream"},
        ],
    },
    "profile": {
        "@context": "https://schema.org/",
        "@type":    "Dataset",
        "@id":      _QID,
        "name":        "ct-seg",
        "description": "Model run of ct-seg",
        "url":         "ghcr.io/example/ct-seg",
    },
    "provenance": {
        "@id": "#run",
        "@type": "prov:Activity",
        "prov:startedAtTime": "2026-05-15T10:00:00Z",
        "prov:endedAtTime": "2026-05-15T11:00:42Z",
        "prov:wasAssociatedWith": {
            "@id": "ghcr.io/example/ct-seg:2.1.0",
            "@type": "prov:SoftwareAgent",
            "schema:name": "ct-seg",
            "schema:softwareVersion": "2.1.0",
            "schema:url": "ghcr.io/example/ct-seg",
        },
        "prov:used": [],
    },
}


_ROCRATE_BYTES = b'{"@context": "https://w3id.org/ro/crate/1.1/context", "@graph": []}'


class TestGetRunMetadata:
    def _mock_bytes_object(self, data: bytes):
        reader = MagicMock()
        reader.__enter__ = lambda s: s
        reader.__exit__ = MagicMock(return_value=False)
        reader.read.return_value = data
        obj = MagicMock()
        obj.reader.return_value = reader
        return obj

    def _mock_object(self, fdo: dict):
        import json
        return self._mock_bytes_object(json.dumps(fdo).encode())

    def _branch_mock_with_rocrate(self, mock_repo, fdo_obj, rocrate_bytes=_ROCRATE_BYTES):
        branch_mock = mock_repo.return_value.branch.return_value
        rocrate_obj = self._mock_bytes_object(rocrate_bytes)

        def obj_side_effect(path):
            if path.endswith(".fdo.json"):
                return fdo_obj
            return rocrate_obj

        branch_mock.object.side_effect = obj_side_effect
        return branch_mock

    def _branch_mock_no_rocrate(self, mock_repo, fdo_obj):
        from lakefs.exceptions import ObjectNotFoundException
        branch_mock = mock_repo.return_value.branch.return_value

        def obj_side_effect(path):
            if path.endswith(".fdo.json"):
                return fdo_obj
            raise ObjectNotFoundException

        branch_mock.object.side_effect = obj_side_effect
        return branch_mock

    def test_extracts_all_fields(self):
        obj = self._mock_object(_FDO)
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            self._branch_mock_with_rocrate(mock_repo, obj)
            result = get_run_metadata(_QID, "model-runs")

        import json as _json
        assert result["model_name"]       == "ct-seg"
        assert result["qid"]              == _QID
        assert result["docker_tag"]       == "2.1.0"
        assert result["run_timestamp"]    == "2026-05-15T11:00:42Z"
        assert result["status"]           == ""
        assert result["computation_time"] == ""
        assert result["input_files"]      == [_INPUT_URL]
        assert result["output_files"]     == [_OUTPUT_URL]
        assert _json.loads(result["fdo_bytes"]) == _FDO
        assert result["rocrate_bytes"]    == _ROCRATE_BYTES

    def test_reads_correct_object_paths(self):
        obj = self._mock_object(_FDO)
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            branch_mock = self._branch_mock_with_rocrate(mock_repo, obj)
            get_run_metadata(_QID, "model-runs")

        paths_called = [call.args[0] for call in branch_mock.object.call_args_list]
        assert f"{shard_qid(_QID)}/{_QID}.fdo.json" in paths_called
        assert f"{shard_qid(_QID)}/components/ro-crate-metadata.json" in paths_called

    def test_rocrate_missing_returns_empty_bytes(self):
        obj = self._mock_object(_FDO)
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            self._branch_mock_no_rocrate(mock_repo, obj)
            result = get_run_metadata(_QID, "model-runs")

        assert result["rocrate_bytes"] == b""

    def test_missing_optional_fields_default_to_empty_string(self):
        sparse_fdo = {"@id": _QID, "@type": "DigitalObject", "kernel": {}, "profile": {}, "provenance": {}}
        obj = self._mock_object(sparse_fdo)
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            self._branch_mock_with_rocrate(mock_repo, obj)
            result = get_run_metadata(_QID, "model-runs")

        assert result["model_name"]       == ""
        assert result["qid"]              == _QID
        assert result["docker_tag"]       == ""
        assert result["run_timestamp"]    == ""
        assert result["status"]           == ""
        assert result["computation_time"] == ""
        assert result["fdo_bytes"]        != b""
        assert result["input_files"]      == []
        assert result["output_files"]     == []

    def test_raises_on_missing_id(self):
        obj = self._mock_object({"@type": "DigitalObject"})
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            branch_mock = mock_repo.return_value.branch.return_value
            branch_mock.object.return_value = obj
            with pytest.raises(ValueError, match="no @id"):
                get_run_metadata(_QID, "model-runs")
