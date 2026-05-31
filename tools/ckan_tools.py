"""CKAN helper functions and dataset creation utilities."""

import os
from urllib.parse import parse_qs, unquote, urlparse

import requests

def _ckan_url() -> str:
    return os.environ["CKAN_HOST"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ckan_headers() -> dict:
    return {"Authorization": os.environ["CKAN_API_TOKEN"]}


def _parse_json(r: requests.Response, context: str) -> dict:
    try:
        return r.json()
    except Exception:
        raise RuntimeError(
            f"CKAN {context} returned non-JSON (HTTP {r.status_code} from {r.url}): {r.text[:200]!r}"
        )


def _ckan_api(action: str, payload: dict) -> dict:
    r = requests.post(
        f"{_ckan_url()}/api/3/action/{action}",
        headers=_ckan_headers(),
        json=payload,
    )
    body = _parse_json(r, action)
    if not body["success"]:
        raise RuntimeError(f"CKAN {action} failed: {body['error']}")
    return body["result"]


def _ckan_upload_resource(pkg_id: str, filename: str, content: bytes, description: str) -> None:
    r = requests.post(
        f"{_ckan_url()}/api/3/action/resource_create",
        headers=_ckan_headers(),
        data={
            "package_id":  pkg_id,
            "name":        filename,
            "format":      "JSON",
            "description": description,
        },
        files={"upload": (filename, content, "application/json")},
    )
    body = _parse_json(r, "resource_create (upload)")
    if not body["success"]:
        raise RuntimeError(f"CKAN resource_create (upload) failed: {body['error']}")


def _vocabs() -> dict:
    """Return {vocab_name: vocab_id} for all registered tag vocabularies."""
    r = requests.get(f"{_ckan_url()}/api/3/action/vocabulary_list")
    return {v["name"]: v["id"] for v in _parse_json(r, "vocabulary_list")["result"]}


def _filename_from_url(url: str) -> str:
    path = unquote(parse_qs(urlparse(url).query).get("path", [""])[0])
    return path.split("/")[-1] if path else url.split("/")[-1]


def _vtag(vocabs: dict, vocab: str, value: str) -> dict:
    return {"name": value, "vocabulary_id": vocabs[vocab]}


def _ckan_delete_run(run_id: str) -> None:
    """Delete a CKAN dataset by run_id (no-op if it does not exist)."""
    _ckan_api("package_delete", {"id": run_id.lower()})


def _ckan_run_exists(run_id: str) -> bool:
    """Return True if a CKAN dataset with extras_run_id == run_id already exists."""
    r = requests.get(
        f"{_ckan_url()}/api/3/action/package_search",
        params={"q": f"extras_run_id:{run_id}", "rows": 1},
    )
    return _parse_json(r, "package_search")["result"]["count"] > 0


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
    r = requests.get(f"{_ckan_url()}/api/3/action/package_show?id={name}")
    body = _parse_json(r, "package_show")
    if body["success"]:
        return body["result"]

    vocabs = _vocabs()
    return _ckan_api("package_create", {
        "name":      name,
        "title":     name,
        "notes":     f"{description}\n\n### [Browse all runs →]({_ckan_url()}/dataset?q=extras_model:{name})",
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
    qid: str,
    git_commit: str,
    docker_tag: str,
    run_timestamp: str,
    status: str,
    computation_time: str,
    rocrate_bytes: bytes,
    input_files: list,
    output_files: list,
) -> dict:
    """
    Create a model run dataset in CKAN and attach all input and output files
    as resources.

    rocrate_bytes is uploaded as an actual file to CKAN.
    input_files / output_files are lists of lakeFS HTTP API URLs stored as
    resource source URLs.

    The dataset is placed in the type-model-run group and carries extras.model
    pointing to the model descriptor, enabling run discovery via
    package_search?q=extras_model:<model_name>.
    """
    vocabs = _vocabs()

    pkg = _ckan_api("package_create", {
        "name":      run_id.lower(),
        "title":     f"{model_name} · {run_id}",
        "notes":     f"Model run {run_id} of {model_name}.",
        "owner_org": "episerve",
        "groups":    [{"name": "type-model-run"}],
        "tags": [
            _vtag(vocabs, "status", status),
        ] if status else [],
        "extras": [
            {"key": "run_id",          "value": run_id},
            {"key": "qid",             "value": qid},
            {"key": "model",           "value": model_name},
            {"key": "git_commit",      "value": git_commit},
            {"key": "docker_tag",      "value": docker_tag},
            {"key": "run_timestamp",   "value": run_timestamp},
            {"key": "status",          "value": status},
            {"key": "computation_time","value": computation_time},
        ],
    })

    if rocrate_bytes:
        _ckan_upload_resource(pkg["id"], "ro-crate-metadata.json", rocrate_bytes, "RO-Crate metadata")

    for url in input_files:
        filename = _filename_from_url(url)
        _ckan_api("resource_create", {
            "package_id":  pkg["id"],
            "name":        filename,
            "url":         url,
            "format":      filename.split(".")[-1].upper(),
            "description": "Input file",
        })

    for url in output_files:
        filename = _filename_from_url(url)
        _ckan_api("resource_create", {
            "package_id":  pkg["id"],
            "name":        filename,
            "url":         url,
            "format":      filename.split(".")[-1].upper(),
            "description": "Output file",
        })

    return pkg
