"""Tests for model FDO management: mint_model_qid, ensure_model_fdo, _do_sync_model."""

import json
from unittest.mock import MagicMock, patch

import pytest

import flow.sync_ckan_with_lakefs_models as m
from tools.lakefs_tools import _build_placeholder_model_fdo, ensure_model_fdo, mint_model_qid


@pytest.fixture(autouse=True)
def required_env(monkeypatch):
    monkeypatch.setenv("CKAN_API_TOKEN",    "test-ckan-token")
    monkeypatch.setenv("LAKEFS_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("LAKEFS_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("LAKEFS_HOST",       "http://test-lakefs")


# ── mint_model_qid ─────────────────────────────────────────────────────────────

class TestMintModelQid:
    def test_returns_q_prefix_13_digits(self):
        qid = mint_model_qid("ghcr.io/example/my-model")
        assert qid.startswith("Q")
        assert len(qid) == 14  # "Q" + 13 digits
        assert qid[1:].isdigit()

    def test_is_deterministic(self):
        image = "ghcr.io/example/my-model"
        assert mint_model_qid(image) == mint_model_qid(image)

    def test_differs_for_different_images(self):
        assert mint_model_qid("ghcr.io/example/model-a") != mint_model_qid("ghcr.io/example/model-b")

    def test_tag_not_included_in_input(self):
        # QID is derived from the image URI without tag — caller must not pass tag
        qid_a = mint_model_qid("ghcr.io/example/model")
        qid_b = mint_model_qid("ghcr.io/example/model")
        assert qid_a == qid_b


# ── _build_placeholder_model_fdo ───────────────────────────────────────────────

class TestBuildPlaceholderModelFdo:
    def test_structure(self):
        fdo = _build_placeholder_model_fdo("Q1234567890123", "ghcr.io/ex/model", "model", "1.0")
        assert fdo["@id"] == "Q1234567890123"
        assert fdo["@type"] == "DigitalObject"
        assert fdo["kernel"]["digitalObjectType"] == "https://schema.org/SoftwareApplication"
        assert fdo["profile"]["@type"] == "SoftwareApplication"
        assert fdo["profile"]["name"] == "model"
        assert fdo["profile"]["url"] == "ghcr.io/ex/model"
        assert fdo["profile"]["softwareVersion"] == "1.0"


# ── ensure_model_fdo ───────────────────────────────────────────────────────────

def _make_reader_mock(data: bytes):
    reader = MagicMock()
    reader.__enter__ = lambda s: s
    reader.__exit__ = MagicMock(return_value=False)
    reader.read.return_value = data
    obj = MagicMock()
    obj.reader.return_value = reader
    return obj


class TestEnsureModelFdo:
    def _branch_mock(self, mock_repo, exists=False):
        from lakefs.exceptions import ObjectNotFoundException
        branch = mock_repo.return_value.branch.return_value

        if exists:
            obj = _make_reader_mock(b'{"@id": "Q9999999999999"}')
            branch.object.return_value = obj
        else:
            obj = MagicMock()
            obj.reader.side_effect = ObjectNotFoundException
            branch.object.return_value = obj

        return branch

    def test_returns_existing_qid_without_writing(self):
        image = "ghcr.io/example/model"
        expected_qid = mint_model_qid(image)

        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo:
            self._branch_mock(mock_repo, exists=True)
            result = ensure_model_fdo(image, "model", "1.0", "models")

        assert result == expected_qid
        # branch.commit should NOT have been called
        branch = mock_repo.return_value.branch.return_value
        branch.commit.assert_not_called()

    def test_writes_placeholder_when_no_github_fdo(self):
        image = "ghcr.io/example/model"
        expected_qid = mint_model_qid(image)

        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo, \
             patch("tools.lakefs_tools.get_repo_fdo", return_value=None):
            branch = self._branch_mock(mock_repo, exists=False)
            result = ensure_model_fdo(image, "model", "1.0", "models")

        assert result == expected_qid
        uploaded = branch.object.return_value.upload.call_args[1]["data"]
        fdo = json.loads(uploaded)
        assert fdo["@id"] == expected_qid
        assert fdo["profile"]["@type"] == "SoftwareApplication"
        branch.commit.assert_called_once()

    def test_uses_github_fdo_when_available(self):
        image = "ghcr.io/example/model"
        expected_qid = mint_model_qid(image)
        github_fdo = {
            "@id": "some-other-id",
            "@type": "DigitalObject",
            "kernel": {"@id": "some-other-id", "primaryIdentifier": "some-other-id"},
            "profile": {"@type": "SoftwareApplication", "name": "model"},
        }

        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo, \
             patch("tools.lakefs_tools.get_repo_fdo", return_value=github_fdo):
            branch = self._branch_mock(mock_repo, exists=False)
            result = ensure_model_fdo(image, "model", "1.0", "models")

        assert result == expected_qid
        uploaded = branch.object.return_value.upload.call_args[1]["data"]
        fdo = json.loads(uploaded)
        assert fdo["@id"] == expected_qid
        assert fdo["kernel"]["@id"] == expected_qid
        assert fdo["kernel"]["primaryIdentifier"] == expected_qid

    def test_force_overwrites_existing(self):
        image = "ghcr.io/example/model"

        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo, \
             patch("tools.lakefs_tools.get_repo_fdo", return_value=None):
            branch = self._branch_mock(mock_repo, exists=True)
            ensure_model_fdo(image, "model", "1.0", "models", force=True)

        # With force=True, should write even if object exists
        branch.object.return_value.upload.assert_called_once()
        branch.commit.assert_called_once()


# ── _do_sync_model ─────────────────────────────────────────────────────────────

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


class TestDoSyncModel:
    def test_calls_create_model_with_metadata(self):
        meta = {
            "name": "my-model",
            "description": "A model.",
            "docker_image": "ghcr.io/ex/my-model",
            "docker_tag": "1.0",
            "git_repo": "",
            "algorithm": "",
            "input_format": "",
            "output_format": "",
            "lead_researcher": "",
            "domain": "",
            "modality": "",
            "paper_doi": "",
            "docker_image_created": "",
        }
        new_pkg = {"id": "new-id", "name": "my-model"}

        not_found = MagicMock()
        not_found.json.return_value = {"success": False, "error": {"message": "Not found"}}

        def get_side(url, **_):
            if "package_show" in url:
                return not_found
            return _vocab_get_response()

        with patch.object(m, "get_model_metadata", return_value=meta), \
             patch("requests.get", side_effect=get_side), \
             patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock()
            mock_post.return_value.json.return_value = {"success": True, "result": new_pkg}
            m._do_sync_model("Q1234567890123", "models")

        payload = mock_post.call_args[1]["json"]
        assert payload["name"] == "my-model"
        extras = {e["key"]: e["value"] for e in payload["extras"]}
        assert extras["model_qid"] == "Q1234567890123"

    def test_skips_on_metadata_error(self):
        with patch.object(m, "get_model_metadata", side_effect=Exception("not found")), \
             patch.object(m, "create_model") as mock_create:
            m._do_sync_model("Q1234567890123", "models")

        mock_create.assert_not_called()
