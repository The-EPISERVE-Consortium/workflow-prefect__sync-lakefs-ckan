"""CKAN helper functions and dataset creation utilities."""

import json
import os
from datetime import datetime
from functools import lru_cache
from urllib.parse import parse_qs, unquote, urlparse

import requests

from tools.github_tools import get_image_created

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


def _ckan_add_preview_view(resource: dict) -> None:
    fmt = resource.get("format", "").upper()
    if fmt == "PARQUET":
        _ckan_api("resource_view_create", {
            "resource_id": resource["id"],
            "view_type":   "parquet_view",
            "title":       "Preview",
        })
    elif fmt in {"JSON", "LOG"}:
        _ckan_api("resource_view_create", {
            "resource_id": resource["id"],
            "view_type":   "text_view",
            "title":       "Preview",
        })


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
    _ckan_add_preview_view(body["result"])


@lru_cache(maxsize=1)
def _vocabs() -> dict:
    """Return {vocab_name: vocab_id} for all registered tag vocabularies."""
    r = requests.get(f"{_ckan_url()}/api/3/action/vocabulary_list")
    return {v["name"]: v["id"] for v in _parse_json(r, "vocabulary_list")["result"]}


def _fmt_ts(ts: str) -> str:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y%m%d-%H%M")
    except (ValueError, TypeError):
        return ts


def _filename_from_url(url: str) -> str:
    path = unquote(parse_qs(urlparse(url).query).get("path", [""])[0])
    return path.split("/")[-1] if path else url.split("/")[-1]


def _vtag(vocabs: dict, vocab: str, value: str) -> dict:
    return {"name": value, "vocabulary_id": vocabs[vocab]}


def _dataset_link(qid: str) -> str:
    """Return a CKAN markdown link for a dataset QID.

    Args:
        qid: Dataset QID to link to.

    Returns:
        str: Markdown link pointing at the CKAN dataset page.
    """
    return f"[{qid}]({_ckan_url().rstrip('/')}/dataset/{qid.lower()})"


def _model_run_notes(
    model_name: str,
    run_id: str,
    qid: str,
    docker_tag: str,
    run_timestamp: str,
    status: str,
    input_files: list,
    output_files: list,
    model_qid: str = "",
    input_dataset_qids: list | None = None,
    data_transformation_sql: list | None = None,
) -> str:
    """Build descriptive CKAN notes for a model-run dataset.

    Args:
        model_name: Human-readable model name.
        run_id: Identifier used as the CKAN dataset name.
        qid: FAIR Digital Object QID for the run.
        docker_tag: Docker image tag used by the run.
        run_timestamp: Timestamp recorded for the run.
        status: Run status inferred from RO-Crate metadata.
        input_files: Input resource URLs attached to the run.
        output_files: Output resource URLs attached to the run.
        model_qid: QID of the associated model descriptor.
        input_dataset_qids: QIDs of datasets used as run inputs.
        data_transformation_sql: SQL statements recorded for input transformations.

    Returns:
        str: Markdown text for CKAN's notes field.
    """
    dataset_qids = input_dataset_qids or []
    sql_statements = [sql for sql in (data_transformation_sql or []) if sql]

    model_text = f"[{model_name}]({_ckan_url().rstrip('/')}/dataset/{model_qid.lower()})" if model_qid else model_name

    if dataset_qids:
        dataset_text = ", ".join(_dataset_link(dataset_qid) for dataset_qid in dataset_qids)
        first_sentence = f"This item represents a run of model {model_text} applied on dataset {dataset_text}."
    else:
        first_sentence = f"This item represents a run of model {model_text}."

    lines = [first_sentence]

    if sql_statements:
        lines.append("")
        lines.append("Recorded data transformation SQL:")
        for sql in sql_statements:
            lines.extend(["", "```sql", sql, "```"])

    return "\n".join(lines)


def _model_notes(name: str, description: str, model_qid: str) -> str:
    """Build descriptive CKAN notes for a model descriptor dataset.

    Args:
        name: Human-readable model name.
        description: Model description from the FDO profile.
        model_qid: QID of the model descriptor.

    Returns:
        str: Markdown text for CKAN's notes field.
    """
    lines = [
        f"This item represents the model {name}.",
    ]
    if description:
        lines.extend(["", description])
    lines.extend([
        "",
        "Model runs created with this descriptor are linked below.",
    ])
    lines.extend([
        "",
        f"### [Browse all runs →]({_ckan_url()}/dataset?q=extras_model_qid:{model_qid})",
    ])
    return "\n".join(lines)


def _ckan_delete_run(run_id: str) -> None:
    """Delete a CKAN dataset by run_id (no-op if it does not exist)."""
    _ckan_api("package_delete", {"id": run_id.lower()})


def _ckan_run_exists(run_id: str) -> bool:
    """Return True if a CKAN dataset with extras_qid == run_id already exists."""
    r = requests.get(
        f"{_ckan_url()}/api/3/action/package_search",
        params={"q": f"extras_qid:{run_id}", "rows": 1},
    )
    return _parse_json(r, "package_search")["result"]["count"] > 0


def _ckan_raw_dataset_exists(qid: str) -> bool:
    """Return True if a CKAN dataset with extras_qid == qid already exists."""
    r = requests.get(
        f"{_ckan_url()}/api/3/action/package_search",
        params={"q": f"extras_qid:{qid}", "rows": 1},
    )
    return _parse_json(r, "package_search")["result"]["count"] > 0


def _ckan_delete_raw_dataset(qid: str) -> None:
    """Delete a CKAN raw dataset by QID (no-op if it does not exist)."""
    _ckan_api("package_delete", {"id": qid.lower()})


def _ckan_fetch_raw_dataset(qid: str) -> dict | None:
    """Return the CKAN package dict for extras_qid == qid, or None if not found."""
    r = requests.get(
        f"{_ckan_url()}/api/3/action/package_search",
        params={"q": f"extras_qid:{qid}", "rows": 1},
    )
    result = _parse_json(r, "package_search")["result"]
    if result["count"] == 0:
        return None
    return result["results"][0]


def update_raw_dataset(
    qid: str,
    name: str,
    description: str,
    source_url: str,
    modified: str,
    additional_type: str = "",
    source_changed_at: str = "",
    license_id: str = "",
    attribution: str = "",
) -> dict:
    """
    Patch a CKAN raw dataset with only the fields that differ from the current state.

    Resources are not touched. Returns a dict mapping each changed field name to
    (old_value, new_value). Empty dict means nothing changed.

    license_id / attribution are only ever set, never cleared: a blank value
    from the FDO leaves whatever CKAN already has untouched.
    """
    pkg = _ckan_fetch_raw_dataset(qid)
    if pkg is None:
        raise RuntimeError(f"update_raw_dataset called on non-existent dataset {qid}")

    current_extras = {e["key"]: e["value"] for e in pkg.get("extras", [])}
    desired_extras = {
        "dataset_type":       "raw-data",
        "qid":                qid,
        "modified":           modified,
        "additional_type":    additional_type,
        "source_changed_at": source_changed_at,
    }
    if attribution:
        desired_extras["attribution"] = attribution

    changed: dict = {}
    for field, desired in [("title", name), ("notes", description), ("url", source_url)]:
        current = pkg.get(field, "")
        if current != desired:
            changed[field] = (current, desired)
    if license_id and pkg.get("license_id", "") != license_id:
        changed["license_id"] = (pkg.get("license_id", ""), license_id)
    for key, desired in desired_extras.items():
        current = current_extras.get(key, "")
        if current != desired:
            changed[key] = (current, desired)

    if not changed:
        return {}

    patch: dict = {"id": pkg["id"]}
    if any(f in changed for f in ("title", "notes", "url")):
        patch["title"] = name
        patch["notes"] = description
        patch["url"]   = source_url
    if "license_id" in changed:
        patch["license_id"] = license_id
    if any(k in changed for k in desired_extras):
        # package_patch replaces the full extras list — always send all managed keys.
        # Any custom extras added via the CKAN UI will be lost when this branch runs.
        patch["extras"] = [{"key": k, "value": v} for k, v in desired_extras.items()]

    _ckan_api("package_patch", patch)
    return changed


def touch_raw_dataset_modified_if_changed(
    qid: str,
    modified: str,
    additional_type: str = "",
    source_changed_at: str = "",
) -> bool:
    """
    Patch the CKAN extras (dataset_type/qid/modified/additional_type/source_changed_at)
    on an existing raw dataset, but only if the FDO's provenance.source_changed_at
    differs from what CKAN currently has — i.e. only when the underlying data
    actually changed, not merely because the nightly pipeline ran again
    (which always bumps `modified`). One read, at most one write.

    Returns True if a patch was sent, False if already up to date.
    """
    pkg = _ckan_fetch_raw_dataset(qid)
    if pkg is None:
        raise RuntimeError(f"touch_raw_dataset_modified_if_changed called on non-existent dataset {qid}")

    current_source_changed_at = {e["key"]: e["value"] for e in pkg.get("extras", [])}.get("source_changed_at", "")
    if current_source_changed_at == source_changed_at:
        return False

    _ckan_api("package_patch", {
        "id": pkg["id"],
        "extras": [
            {"key": "dataset_type",       "value": "raw-data"},
            {"key": "qid",                "value": qid},
            {"key": "modified",           "value": modified},
            {"key": "additional_type",    "value": additional_type},
            {"key": "source_changed_at", "value": source_changed_at},
        ],
    })
    return True


# ── Dataset creation ───────────────────────────────────────────────────────────

def create_model(
    name: str,
    description: str,
    git_repo: str,
    docker_image: str,
    docker_tag: str,
    algorithm: str,
    input_format: str,
    output_format: str,
    lead_researcher: str,
    domain: str = "",
    modality: str = "",
    paper_doi: str = "",
    docker_image_created: str = "",
    model_qid: str = "",
    fdo_bytes: bytes = b"",
    additional_properties: list = [],
    force_recreate: bool = False,
) -> dict:
    """
    Create a model descriptor dataset in CKAN.

    Idempotent: if a dataset with this name already exists, returns it unchanged.
    The dataset is placed in the type-model group. The git_repo is stored in the
    standard CKAN url field (shown as 'Source'). The description includes a
    'Browse all runs' link pointing to the filtered run search.
    """
    slug = model_qid.lower()
    r = requests.get(f"{_ckan_url()}/api/3/action/package_show?id={slug}")
    body = _parse_json(r, "package_show")
    if body["success"]:
        if not force_recreate:
            pkg = body["result"]
            patch = {}
            # Keep the CKAN notes/url in step with the model's fdo.json on every
            # sync, not just while the notes still hold the placeholder text --
            # otherwise a description change in fdo.json never reaches CKAN
            # without a (concurrency-unsafe) force_recreate.
            if description:
                desired_notes = _model_notes(name, description, model_qid)
                if (pkg.get("notes") or "") != desired_notes:
                    patch["notes"] = desired_notes
            if git_repo and pkg.get("url") != git_repo:
                patch["url"] = git_repo
            if patch:
                patch["id"] = slug
                pkg = _ckan_api("package_patch", patch)
            return pkg
        _ckan_api("package_delete", {"id": slug})

    vocabs = _vocabs()
    pkg = _ckan_api("package_create", {
        "name":      slug,
        "title":     name,
        "notes":     _model_notes(name, description, model_qid),
        "owner_org": "episerve",
        "url":       git_repo,
        "groups":    [{"name": "type-model"}],
        "tags": [t for t in [
            _vtag(vocabs, "domain",   domain)   if domain   else None,
            _vtag(vocabs, "modality", modality) if modality else None,
        ] if t is not None],
        "extras": [
            {"key": "dataset_type",        "value": "model"},
            {"key": "docker_image",        "value": docker_image},
            {"key": "docker_tag",          "value": docker_tag},
            {"key": "docker_image_created","value": docker_image_created},
            {"key": "model_qid",           "value": model_qid},
            {"key": "algorithm",           "value": algorithm},
            {"key": "input_format",        "value": input_format},
            {"key": "output_format",       "value": output_format},
            {"key": "lead_researcher",     "value": lead_researcher},
            {"key": "paper_doi",           "value": paper_doi},
            *([{"key": "model_parameters", "value": json.dumps(additional_properties)}]
              if additional_properties else []),
        ],
    })
    if fdo_bytes and model_qid:
        _ckan_upload_resource(pkg["id"], f"{model_qid}.fdo.json", fdo_bytes, "FDO metadata")
    return pkg


def ensure_model(model_name: str, docker_image: str = "", docker_tag: str = "", model_qid: str = "", fdo_bytes: bytes = b"", description: str = "", git_repo: str = "", additional_properties: list = [], force_recreate: bool = False) -> dict:
    """
    Ensure a model descriptor exists in CKAN, creating a placeholder if not.
    When description/git_repo are provided they are used; otherwise a placeholder
    description is written and the url is left empty for a human to fill in.
    If the model already exists with a placeholder, it is patched in-place.
    """
    return create_model(
        name                  = model_name,
        description           = description or f"Auto-created placeholder for model '{model_name}'.",
        git_repo              = git_repo,
        docker_image          = docker_image,
        docker_tag            = docker_tag,
        docker_image_created  = get_image_created(docker_image, docker_tag),
        model_qid             = model_qid,
        fdo_bytes             = fdo_bytes,
        algorithm             = "",
        input_format          = "",
        output_format         = "",
        lead_researcher       = "",
        additional_properties = additional_properties,
        force_recreate        = force_recreate,
    )


def create_model_run(
    model_name: str,
    run_id: str,
    qid: str,
    docker_tag: str,
    run_timestamp: str,
    status: str,
    computation_time: str,
    fdo_bytes: bytes,
    rocrate_bytes: bytes,
    input_files: list,
    output_files: list,
    model_qid: str = "",
    input_dataset_qids: list = [],
    data_transformation_sql: list = [],
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
        "title":     f"Model-run with model: {model_name} [{_fmt_ts(run_timestamp)}]",
        "notes":     _model_run_notes(
            model_name              = model_name,
            run_id                  = run_id,
            qid                     = qid,
            docker_tag              = docker_tag,
            run_timestamp           = run_timestamp,
            status                  = status,
            input_files             = input_files,
            output_files            = output_files,
            model_qid               = model_qid,
            input_dataset_qids      = input_dataset_qids,
            data_transformation_sql = data_transformation_sql,
        ),
        "owner_org": "episerve",
        "groups":    [{"name": "type-model-run"}],
        "tags": [
            _vtag(vocabs, "status", status),
        ] if status else [],
        "extras": [
            {"key": "qid",                  "value": qid},
            {"key": "model",                "value": model_name},
            {"key": "model_qid",            "value": model_qid},
            {"key": "docker_tag",           "value": docker_tag},
            {"key": "run_timestamp",        "value": run_timestamp},
            {"key": "status",               "value": status},
            {"key": "computation_time",     "value": computation_time},
            *([{"key": "input_dataset_qids", "value": json.dumps(input_dataset_qids)}]
              if input_dataset_qids else []),
            *([{"key": "data_transformation_sql", "value": json.dumps(data_transformation_sql)}]
              if any(data_transformation_sql) else []),
        ],
    })

    if fdo_bytes:
        _ckan_upload_resource(pkg["id"], f"{qid}.fdo.json", fdo_bytes, "FDO metadata")
    if rocrate_bytes:
        _ckan_upload_resource(pkg["id"], "ro-crate-metadata.json", rocrate_bytes, "RO-Crate metadata")

    for url in input_files:
        filename = _filename_from_url(url)
        resource = _ckan_api("resource_create", {
            "package_id":  pkg["id"],
            "name":        filename,
            "url":         url,
            "format":      filename.split(".")[-1].upper(),
            "description": "Input file",
        })
        _ckan_add_preview_view(resource)

    for url in output_files:
        filename = _filename_from_url(url)
        resource = _ckan_api("resource_create", {
            "package_id":  pkg["id"],
            "name":        filename,
            "url":         url,
            "format":      filename.split(".")[-1].upper(),
            "description": "Output file",
        })
        _ckan_add_preview_view(resource)

    return pkg


def create_raw_dataset(
    qid: str,
    name: str,
    description: str,
    source_url: str,
    modified: str,
    components: list,
    fdo_bytes: bytes,
    additional_type: str = "",
    source_changed_at: str = "",
    license_id: str = "",
    attribution: str = "",
) -> dict:
    """
    Create a raw dataset in CKAN and attach all data files as resources.

    fdo_bytes is uploaded as an actual file to CKAN.
    components is a list of dicts with keys filename, url, and media_type.

    license_id (when set) populates CKAN's native license field; attribution
    (when set) is stored as an extra so the credit line shows in the frontend.

    The dataset is placed in the type-raw-data group.
    """
    extras = [
        {"key": "dataset_type",       "value": "raw-data"},
        {"key": "qid",                "value": qid},
        {"key": "modified",           "value": modified},
        {"key": "additional_type",    "value": additional_type},
        {"key": "source_changed_at", "value": source_changed_at},
    ]
    if attribution:
        extras.append({"key": "attribution", "value": attribution})

    package = {
        "name":      qid.lower(),
        "title":     name,
        "notes":     description,
        "owner_org": "episerve",
        "url":       source_url,
        "groups":    [{"name": "type-raw-data"}],
        "extras":    extras,
    }
    if license_id:
        package["license_id"] = license_id

    pkg = _ckan_api("package_create", package)

    if fdo_bytes:
        _ckan_upload_resource(pkg["id"], "fdo.json", fdo_bytes, "FDO metadata")

    for comp in components:
        resource = _ckan_api("resource_create", {
            "package_id":  pkg["id"],
            "name":        comp["filename"],
            "url":         comp["url"],
            "format":      comp["filename"].split(".")[-1].upper(),
            "description": "Data file",
        })
        _ckan_add_preview_view(resource)

    return pkg
