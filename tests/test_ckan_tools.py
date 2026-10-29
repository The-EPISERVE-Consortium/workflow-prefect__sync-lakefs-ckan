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


# ── model card extras ─────────────────────────────────────────────────────────

import json  # noqa: E402
from importlib.resources import files  # noqa: E402


def _card():
    path = files("episerve_specs") / "specs/model_card/examples/model__prediction__generic__timesfm.json"
    return json.loads(path.read_text())


def test_model_card_extras_hold_sections_and_validation():
    card = _card()
    extras = ckan_tools.model_card_extras(card)
    assert json.loads(extras["model_parameters"]) == card["episerve:parameters"]
    assert json.loads(extras["model_inputs"]) == card["episerve:inputs"]
    assert json.loads(extras["model_outputs"]) == card["episerve:outputs"]
    assert json.loads(extras["model_task"]) == card["episerve:task"]
    assert extras["model_card_valid"] == "true"
    validation = json.loads(extras["model_card_validation"])
    assert validation["spec"] == "model-card"
    assert {i["path"] for i in validation["issues"]} == {"license", "episerve:resources"}


def test_invalid_card_is_marked_not_rejected():
    card = _card()
    del card["episerve:parameters"]
    extras = ckan_tools.model_card_extras(card)
    assert "model_parameters" not in extras
    assert extras["model_card_valid"] == "false"
    assert any("'episerve:parameters' is a required property" in i["message"]
               for i in json.loads(extras["model_card_validation"])["issues"])


def test_placeholder_profile_without_conforms_to_is_checked_as_a_model_card():
    extras = ckan_tools.model_card_extras({"@type": "SoftwareApplication", "name": "m", "url": "ghcr.io/x/m"})
    assert extras["model_card_valid"] == "false"
    assert json.loads(extras["model_card_validation"])["spec"] == "model-card"


def test_unknown_spec_version_is_an_issue():
    card = _card()
    card["conformsTo"] = "https://api.episerve.zib.de/schemas/model-card/v9.json"
    extras = ckan_tools.model_card_extras(card)
    assert extras["model_card_valid"] == "false"
    assert json.loads(extras["model_card_validation"])["issues"][0]["path"] == "conformsTo"


def test_new_model_gets_card_extras():
    card = _card()
    with patch("tools.ckan_tools.requests.get") as g, \
         patch("tools.ckan_tools._ckan_api") as api, \
         patch("tools.ckan_tools._vocabs", return_value={}):
        g.return_value.json.return_value = {"success": False}
        api.return_value = {"id": "q1"}
        ckan_tools.create_model(
            name="m", description="d", git_repo="", docker_image="ghcr.io/x/m",
            docker_tag="latest", algorithm="", input_format="", output_format="",
            lead_researcher="", model_qid="Q1", model_card=card,
        )
    payload = next(c.args[1] for c in api.call_args_list if c.args[0] == "package_create")
    extras = {e["key"]: e["value"] for e in payload["extras"]}
    assert json.loads(extras["model_parameters"]) == card["episerve:parameters"]
    assert extras["model_card_valid"] == "true"


def test_existing_model_card_extras_replaced_and_others_kept():
    card = _card()
    existing = _existing(notes=ckan_tools._model_notes("m", "d", "Q1"), url="https://github.com/x/m")
    existing["result"]["extras"] = [
        {"key": "docker_tag", "value": "latest"},
        {"key": "model_parameters", "value": "[]"},
        {"key": "model_resources", "value": "{}"},
    ]
    with patch("tools.ckan_tools.requests.get") as g, \
         patch("tools.ckan_tools._ckan_api") as api:
        g.return_value.json.return_value = existing
        api.return_value = {"id": "q1"}
        ckan_tools.create_model(
            name="m", description="d", git_repo="https://github.com/x/m", docker_image="ghcr.io/x/m",
            docker_tag="latest", algorithm="", input_format="", output_format="",
            lead_researcher="", model_qid="Q1", model_card=card,
        )
    patch_calls = [c for c in api.call_args_list if c.args[0] == "package_patch"]
    assert len(patch_calls) == 1
    extras = {e["key"]: e["value"] for e in patch_calls[0].args[1]["extras"]}
    assert extras["docker_tag"] == "latest"
    assert json.loads(extras["model_parameters"]) == card["episerve:parameters"]
    assert json.loads(extras["model_resources"]) == card["episerve:resources"]


def test_existing_model_card_extras_in_sync_not_patched():
    card = _card()
    existing = _existing(notes=ckan_tools._model_notes("m", "d", "Q1"), url="https://github.com/x/m")
    existing["result"]["extras"] = [{"key": k, "value": v} for k, v in ckan_tools.model_card_extras(card).items()]
    with patch("tools.ckan_tools.requests.get") as g, \
         patch("tools.ckan_tools._ckan_api") as api:
        g.return_value.json.return_value = existing
        ckan_tools.create_model(
            name="m", description="d", git_repo="https://github.com/x/m", docker_image="ghcr.io/x/m",
            docker_tag="latest", algorithm="", input_format="", output_format="",
            lead_researcher="", model_qid="Q1", model_card=card,
        )
    assert not any(c.args[0] == "package_patch" for c in api.call_args_list)
