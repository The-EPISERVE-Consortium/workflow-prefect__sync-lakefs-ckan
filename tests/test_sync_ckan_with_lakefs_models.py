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

    def test_returns_existing_without_writing_when_repo_has_no_card(self):
        image = "ghcr.io/example/model"
        expected_qid = mint_model_qid(image)

        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo, \
             patch("tools.lakefs_tools.get_repo_fdo", return_value=None):
            self._branch_mock(mock_repo, exists=True)
            qid, fdo_bytes = ensure_model_fdo(image, "model", "1.0", "models")

        assert qid == expected_qid
        assert fdo_bytes == b'{"@id": "Q9999999999999"}'
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
            qid, fdo_bytes = ensure_model_fdo(image, "model", "1.0", "models")

        assert qid == expected_qid
        assert len(fdo_bytes) > 0
        uploaded = branch.object.return_value.upload.call_args[1]["data"]
        fdo = json.loads(uploaded)
        assert fdo["@id"] == expected_qid
        assert fdo["profile"]["@type"] == "SoftwareApplication"
        branch.commit.assert_called_once()

    def test_uses_repo_metadata_when_available(self):
        image = "ghcr.io/example/model"
        expected_qid = mint_model_qid(image)
        # repo fdo.json is a plain schema.org descriptor — no @id, no kernel
        repo_metadata = {
            "@context": "https://schema.org/",
            "@type": "SoftwareApplication",
            "name": "my-model",
            "description": "A real description from the repo.",
            "codeRepository": "https://github.com/example/model",
        }

        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo, \
             patch("tools.lakefs_tools.get_repo_fdo", return_value=repo_metadata):
            branch = self._branch_mock(mock_repo, exists=False)
            qid, fdo_bytes = ensure_model_fdo(image, "model", "1.0", "models")

        assert qid == expected_qid
        assert len(fdo_bytes) > 0
        uploaded = branch.object.return_value.upload.call_args[1]["data"]
        fdo = json.loads(uploaded)
        # QID always comes from the sync workflow, never from the repo file
        assert fdo["@id"] == expected_qid
        assert fdo["kernel"]["@id"] == expected_qid
        assert fdo["kernel"]["primaryIdentifier"] == expected_qid
        # Profile fields are merged from the repo file
        assert fdo["profile"]["description"] == "A real description from the repo."
        assert fdo["profile"]["name"] == "my-model"
        assert fdo["profile"]["codeRepository"] == "https://github.com/example/model"
        # QID-specific identity fields are never overwritten by the repo file
        assert fdo["profile"]["@id"] == expected_qid

    def _existing_branch(self, mock_repo, fdo: dict):
        branch = mock_repo.return_value.branch.return_value
        branch.object.return_value = _make_reader_mock(json.dumps(fdo).encode())
        return branch

    def test_existing_fdo_updated_when_card_changed(self):
        image = "ghcr.io/example/model"
        qid = mint_model_qid(image)
        old = _build_placeholder_model_fdo(qid, image, "model", "1.0")
        old["profile"]["additionalProperty"] = [{"name": "horizon"}]  # a field the card no longer has
        card = {"@type": "SoftwareApplication", "name": "model", "description": "New.",
                "episerve:parameters": {"type": "object", "properties": {}}}

        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo, \
             patch("tools.lakefs_tools.get_repo_fdo", return_value=card):
            branch = self._existing_branch(mock_repo, old)
            _, fdo_bytes = ensure_model_fdo(image, "model", "2.0", "models")

        fdo = json.loads(branch.object.return_value.upload.call_args[1]["data"])
        assert fdo_bytes == branch.object.return_value.upload.call_args[1]["data"]
        assert fdo["profile"]["description"] == "New."
        assert fdo["profile"]["episerve:parameters"] == card["episerve:parameters"]
        assert "additionalProperty" not in fdo["profile"]
        assert fdo["profile"]["@id"] == qid
        assert fdo["profile"]["softwareVersion"] == "1.0"  # kept from the first registration
        assert fdo["provenance"] == old["provenance"]
        branch.commit.assert_called_once()

    def test_existing_fdo_kept_when_card_unchanged(self):
        image = "ghcr.io/example/model"
        qid = mint_model_qid(image)
        card = {"@type": "SoftwareApplication", "name": "model", "description": "Same."}
        fdo = _build_placeholder_model_fdo(qid, image, "model", "1.0")
        fdo["profile"].update(card)

        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo, \
             patch("tools.lakefs_tools.get_repo_fdo", return_value=card):
            branch = self._existing_branch(mock_repo, fdo)
            ensure_model_fdo(image, "model", "2.0", "models")

        branch.object.return_value.upload.assert_not_called()
        branch.commit.assert_not_called()

    def test_existing_fdo_not_refreshed_without_refresh(self):
        image = "ghcr.io/example/model"
        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo, \
             patch("tools.lakefs_tools.get_repo_fdo") as mock_card:
            branch = self._branch_mock(mock_repo, exists=True)
            _, fdo_bytes = ensure_model_fdo(image, "model", "1.0", "models", refresh=False)

        assert fdo_bytes == b'{"@id": "Q9999999999999"}'
        mock_card.assert_not_called()
        branch.commit.assert_not_called()

    def test_force_overwrites_existing(self):
        image = "ghcr.io/example/model"

        with patch("tools.lakefs_tools._lakefs_client"), \
             patch("tools.lakefs_tools.lakefs.Repository") as mock_repo, \
             patch("tools.lakefs_tools.get_repo_fdo", return_value=None):
            branch = self._branch_mock(mock_repo, exists=True)
            ensure_model_fdo(image, "model", "1.0", "models", force=True)

        # With force=True, should write even if object exists
        branch.object.return_value.upload.assert_called_once()
        # Upload must skip the presigned PUT (Ceph RGW / Squid 403s the
        # Content-Type signed-header mismatch).
        assert branch.object.return_value.upload.call_args[1]["pre_sign"] is False
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
            "fdo_bytes": b"",
        }
        new_pkg = {"id": "new-id", "name": "my-model"}

        not_found = MagicMock()
        not_found.json.return_value = {"success": False, "error": {"message": "Not found"}}

        def get_side(url, **_):
            if "package_show" in url:
                return not_found
            return _vocab_get_response()

        with patch.object(m, "get_model_metadata", return_value=meta), \
             patch.object(m, "ensure_model_fdo") as mock_fdo, \
             patch("requests.get", side_effect=get_side), \
             patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock()
            mock_post.return_value.json.return_value = {"success": True, "result": new_pkg}
            m._do_sync_model("Q1234567890123", "models")

        payload = mock_post.call_args[1]["json"]
        assert payload["name"]  == "q1234567890123"
        assert payload["title"] == "my-model"
        extras = {e["key"]: e["value"] for e in payload["extras"]}
        assert extras["model_qid"] == "Q1234567890123"
        # the lakeFS FDO is first brought in step with the model card
        mock_fdo.assert_called_once()
        assert mock_fdo.call_args.kwargs["docker_image"] == "ghcr.io/ex/my-model"

    def test_skips_on_metadata_error(self):
        with patch.object(m, "get_model_metadata", side_effect=Exception("not found")), \
             patch.object(m, "create_model") as mock_create:
            m._do_sync_model("Q1234567890123", "models")

        mock_create.assert_not_called()

    def test_image_created_read_from_registry_with_latest_as_default_tag(self):
        meta = {"name": "my-model", "docker_image": "ghcr.io/ex/my-model", "docker_tag": "",
                "docker_image_created": "", "fdo_bytes": b""}
        with patch.object(m, "get_model_metadata", return_value=meta), \
             patch.object(m, "ensure_model_fdo"), \
             patch.object(m, "get_image_created", return_value="2026-10-30T00:45:26Z") as reg, \
             patch.object(m, "create_model") as mock_create:
            m._do_sync_model("Q1234567890123", "models")

        reg.assert_called_once_with("ghcr.io/ex/my-model", "latest")
        assert mock_create.call_args.kwargs["docker_image_created"] == "2026-10-30T00:45:26Z"

