"""Tests for tools.ckan_tools.create_model idempotent-update behaviour."""

from unittest.mock import patch

import pytest

from tools import ckan_tools


@pytest.fixture(autouse=True)
def _ckan_env(monkeypatch):
    monkeypatch.setenv("CKAN_HOST", "https://ckan.test")
    monkeypatch.setenv("CKAN_API_TOKEN", "tok")


def _existing(notes="", url=""):
    """package_show mock returning an existing model package."""
    return {"success": True, "result": {"id": "q1", "name": "q1", "notes": notes, "url": url}}


def test_existing_model_notes_resynced_when_description_changed():
    desired = ckan_tools._model_notes("m", "NEW description", "Q1")
    with patch("tools.ckan_tools.requests.get") as g, \
         patch("tools.ckan_tools._ckan_api") as api:
        g.return_value.json.return_value = _existing(notes="This item ... OLD description ...")
        g.return_value.status_code = 200
        api.return_value = {"id": "q1"}
        ckan_tools.create_model(
            name="m", description="NEW description", git_repo="", docker_image="ghcr.io/x/m",
            docker_tag="latest", algorithm="", input_format="", output_format="",
            lead_researcher="", model_qid="Q1",
        )
    patch_calls = [c for c in api.call_args_list if c.args[0] == "package_patch"]
    assert len(patch_calls) == 1
    assert patch_calls[0].args[1]["notes"] == desired


def test_existing_model_not_patched_when_already_in_sync():
    notes = ckan_tools._model_notes("m", "same", "Q1")
    with patch("tools.ckan_tools.requests.get") as g, \
         patch("tools.ckan_tools._ckan_api") as api:
        g.return_value.json.return_value = _existing(notes=notes, url="https://github.com/x/m")
        g.return_value.status_code = 200
        ckan_tools.create_model(
            name="m", description="same", git_repo="https://github.com/x/m", docker_image="ghcr.io/x/m",
            docker_tag="latest", algorithm="", input_format="", output_format="",
            lead_researcher="", model_qid="Q1",
        )
    assert not any(c.args[0] == "package_patch" for c in api.call_args_list)


def test_existing_model_url_backfilled_and_updated():
    with patch("tools.ckan_tools.requests.get") as g, \
         patch("tools.ckan_tools._ckan_api") as api:
        g.return_value.json.return_value = _existing(
            notes=ckan_tools._model_notes("m", "d", "Q1"), url="https://old",
        )
        g.return_value.status_code = 200
        api.return_value = {"id": "q1"}
        ckan_tools.create_model(
            name="m", description="d", git_repo="https://github.com/x/m", docker_image="ghcr.io/x/m",
            docker_tag="latest", algorithm="", input_format="", output_format="",
            lead_researcher="", model_qid="Q1",
        )
    patch_calls = [c for c in api.call_args_list if c.args[0] == "package_patch"]
    assert len(patch_calls) == 1
    assert patch_calls[0].args[1]["url"] == "https://github.com/x/m"


def test_notes_links_use_ckan_public_url(monkeypatch):
    monkeypatch.setenv("CKAN_HOST", "http://ckan.ckan.svc.cluster.local")
    monkeypatch.setenv("CKAN_PUBLIC_URL", "https://data.episerve.zib.de")
    notes = ckan_tools._model_notes("m", "d", "Q9")
    assert "https://data.episerve.zib.de/dataset?q=extras_model_qid:Q9" in notes
    assert "svc.cluster.local" not in notes
    assert ckan_tools._dataset_link("Q9").endswith("(https://data.episerve.zib.de/dataset/q9)")


def test_notes_links_fall_back_to_ckan_host(monkeypatch):
    monkeypatch.delenv("CKAN_PUBLIC_URL", raising=False)
    monkeypatch.setenv("CKAN_HOST", "https://ckan.test")
    assert "https://ckan.test/dataset?q=" in ckan_tools._model_notes("m", "d", "Q9")
