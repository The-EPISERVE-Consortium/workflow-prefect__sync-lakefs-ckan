"""Unit tests for sync_ckan_with_lakefs."""

import os
from unittest.mock import MagicMock, patch

import pytest

import flow.sync_ckan_with_lakefs as m
from tools.ckan_tools import (
    _ckan_run_exists,
    create_model,
    create_model_run,
)
from flow.sync_ckan_with_lakefs import sync_run

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


# ── create_model_run ───────────────────────────────────────────────────────────

class TestCreateModelRun:
    def test_calls_package_create_once_and_resource_create_per_file(self):
        pkg = {"id": "pkg-123", "name": "run-2026-001"}
        input_files  = ["lakefs://model-runs/main/run-2026-001/input/config.yaml"]
        output_files = ["lakefs://model-runs/main/run-2026-001/output/result.nii"]

        mock_pkg_resp = _post_response(pkg)
        mock_res_resp = _post_response({})

        with patch("requests.get", return_value=_vocab_get_response()), \
             patch("requests.post") as mock_post:
            mock_post.side_effect = [mock_pkg_resp, mock_res_resp, mock_res_resp]

            result = create_model_run(
                model_name    = "ct-seg",
                run_id        = "run-2026-001",
                git_commit    = "a3f9c12",
                docker_tag    = "2.1.0",
                run_timestamp    = "2026-05-15T11:00:00Z",
                status           = "success",
                computation_time = "",
                input_files   = input_files,
                output_files  = output_files,
            )

        # 1 package_create + 1 input resource + 1 output resource
        assert mock_post.call_count == 3
        assert result == pkg

    def test_creates_one_resource_per_input_and_output(self):
        pkg = {"id": "pkg-456", "name": "run-multi"}

        with patch("requests.get", return_value=_vocab_get_response()), \
             patch("requests.post") as mock_post:
            mock_post.side_effect = [
                _post_response(pkg),
                _post_response({}),  # input 1
                _post_response({}),  # input 2
                _post_response({}),  # output 1
            ]

            create_model_run(
                model_name    = "ct-seg",
                run_id        = "run-multi",
                git_commit    = "abc",
                docker_tag    = "1.0",
                run_timestamp    = "2026-05-15T11:00:00Z",
                status           = "success",
                computation_time = "",
                input_files   = [
                    "lakefs://model-runs/main/run-multi/input/a.yaml",
                    "lakefs://model-runs/main/run-multi/input/b.yaml",
                ],
                output_files  = ["lakefs://model-runs/main/run-multi/output/out.nii"],
            )

        assert mock_post.call_count == 4  # 1 + 2 inputs + 1 output


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
            sync_run("run-2026-001", "model-runs")

        mock_exists.assert_called_once_with("run-2026-001")
        mock_create.assert_not_called()

    def test_creates_run_when_not_in_ckan(self):
        metadata = {
            "model_name":    "ct-seg",
            "git_commit":    "a3f9c12",
            "docker_tag":    "2.1.0",
            "run_timestamp": "2026-05-15T11:00:00Z",
            "status":        "success",
        }
        input_files  = ["lakefs://model-runs/main/run-001/input/config.yaml"]
        output_files = ["lakefs://model-runs/main/run-001/output/result.nii"]

        with patch.object(m, "_ckan_run_exists", return_value=False), \
             patch.object(m, "get_run_metadata", return_value=metadata), \
             patch.object(m, "list_run_files", side_effect=[input_files, output_files]), \
             patch.object(m, "create_model_run") as mock_create:
            sync_run("run-001", "model-runs")

        mock_create.assert_called_once_with(
            model_name       = "ct-seg",
            run_id           = "run-001",
            git_commit       = "a3f9c12",
            docker_tag       = "2.1.0",
            run_timestamp    = "2026-05-15T11:00:00Z",
            status           = "success",
            computation_time = "",
            input_files      = input_files,
            output_files     = output_files,
        )
