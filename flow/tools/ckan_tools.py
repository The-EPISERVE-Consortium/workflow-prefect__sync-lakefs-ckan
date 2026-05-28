"""CKAN helper functions and dataset creation utilities."""

import os

import requests

CKAN_URL = os.environ["CKAN_HOST"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ckan_headers() -> dict:
    return {"Authorization": os.environ["CKAN_API_TOKEN"]}


def _ckan_api(action: str, payload: dict) -> dict:
    r = requests.post(
        f"{CKAN_URL}/api/3/action/{action}",
        headers=_ckan_headers(),
        json=payload,
    ).json()
    if not r["success"]:
        raise RuntimeError(f"CKAN {action} failed: {r['error']}")
    return r["result"]


def _vocabs() -> dict:
    """Return {vocab_name: vocab_id} for all registered tag vocabularies."""
    return {v["name"]: v["id"] for v in
        requests.get(f"{CKAN_URL}/api/3/action/vocabulary_list").json()["result"]}


def _vtag(vocabs: dict, vocab: str, value: str) -> dict:
    return {"name": value, "vocabulary_id": vocabs[vocab]}


def _ckan_run_exists(run_id: str) -> bool:
    """Return True if a CKAN dataset with extras_run_id == run_id already exists."""
    r = requests.get(
        f"{CKAN_URL}/api/3/action/package_search",
        params={"q": f"extras_run_id:{run_id}", "rows": 1},
    )
    return r.json()["result"]["count"] > 0


# ── Dataset creation ───────────────────────────────────────────────────────────

def create_model(
    name: str,
    description: str,
    git_repo: str,
    docker_image: str,
    algorithm: str,
    input_format: str,
    output_format: str,
    lead_researcher: str,
    domain: str,
    modality: str,
    paper_doi: str = "",
) -> dict:
    """
    Create a model descriptor dataset in CKAN.

    Idempotent: if a dataset with this name already exists, returns it unchanged.
    The dataset is placed in the type-model group. The git_repo is stored in the
    standard CKAN url field (shown as 'Source'). The description includes a
    'Browse all runs' link pointing to the filtered run search.
    """
    r = requests.get(f"{CKAN_URL}/api/3/action/package_show?id={name}").json()
    if r["success"]:
        return r["result"]

    vocabs = _vocabs()
    return _ckan_api("package_create", {
        "name":      name,
        "title":     name,
        "notes":     f"{description}\n\n### [Browse all runs →]({CKAN_URL}/dataset?q=extras_model:{name})",
        "owner_org": "episerve",
        "url":       git_repo,
        "groups":    [{"name": "type-model"}],
        "tags": [
            _vtag(vocabs, "domain",   domain),
            _vtag(vocabs, "modality", modality),
        ],
        "extras": [
            {"key": "dataset_type",    "value": "model"},
            {"key": "docker_image",    "value": docker_image},
            {"key": "algorithm",       "value": algorithm},
            {"key": "input_format",    "value": input_format},
            {"key": "output_format",   "value": output_format},
            {"key": "lead_researcher", "value": lead_researcher},
            {"key": "paper_doi",       "value": paper_doi},
        ],
    })


def create_model_run(
    model_name: str,
    run_id: str,
    git_commit: str,
    docker_tag: str,
    run_timestamp: str,
    status: str,
    computation_time: str,
    input_files: list,
    output_files: list,
) -> dict:
    """
    Create a model run dataset in CKAN and attach all input and output files
    as resources with lakeFS URIs.

    input_files / output_files are lists of full lakeFS URIs:
      e.g. ["lakefs://model-runs/main/<run_id>/input/config.yaml"]

    The dataset is placed in the type-model-run group and carries extras.model
    pointing to the model descriptor, enabling run discovery via
    package_search?q=extras_model:<model_name>.
    """
    vocabs = _vocabs()

    pkg = _ckan_api("package_create", {
        "name":      run_id,
        "title":     f"{model_name} · {run_id}",
        "notes":     f"Model run {run_id} of {model_name}.",
        "owner_org": "episerve",
        "groups":    [{"name": "type-model-run"}],
        "tags": [
            _vtag(vocabs, "status", status),
        ] if status else [],
        "extras": [
            {"key": "run_id",        "value": run_id},
            {"key": "model",         "value": model_name},
            {"key": "git_commit",    "value": git_commit},
            {"key": "docker_tag",    "value": docker_tag},
            {"key": "run_timestamp",   "value": run_timestamp},
            {"key": "status",          "value": status},
            {"key": "computation_time","value": computation_time},
        ],
    })

    for uri in input_files:
        filename = uri.split("/")[-1]
        _ckan_api("resource_create", {
            "package_id":  pkg["id"],
            "name":        filename,
            "url":         uri,
            "format":      filename.split(".")[-1].upper(),
            "description": "Input file",
        })

    for uri in output_files:
        filename = uri.split("/")[-1]
        _ckan_api("resource_create", {
            "package_id":  pkg["id"],
            "name":        filename,
            "url":         uri,
            "format":      filename.split(".")[-1].upper(),
            "description": "Output file",
        })

    return pkg
