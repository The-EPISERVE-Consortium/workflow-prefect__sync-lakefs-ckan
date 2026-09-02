"""lakeFS helper functions."""

import hashlib
import json
import os
from datetime import datetime, timezone
from urllib.parse import quote

import lakefs
from lakefs.exceptions import ObjectNotFoundException

from tools.github_tools import get_repo_fdo
from tools.sharding import shard_qid

LAKEFS_BRANCH = "main"


def _lakefs_client() -> lakefs.client.Client:
    return lakefs.client.Client(
        host=os.environ["LAKEFS_HOST"],
        username=os.environ["LAKEFS_ACCESS_KEY"],
        password=os.environ["LAKEFS_SECRET_KEY"],
    )


def _lakefs_object_url(repo: str, path: str) -> str:
    """Build a lakeFS object API URL for a file in the main branch."""
    base = os.environ["LAKEFS_HOST"].rstrip("/")
    return f"{base}/api/v1/repositories/{repo}/refs/{LAKEFS_BRANCH}/objects?path={quote(path, safe='')}&presign=false"


def _doip_public_base() -> str:
    """Base URL for the DOIP retrieve links written into the CKAN catalog.

    Kept separate from ``DOIP_HOST`` -- which may be an in-cluster Service DNS
    used for protocol access -- so a mutable internal host can never end up in a
    public catalog. Falls back to ``DOIP_HOST`` when ``DOIP_PUBLIC_URL`` is unset.
    """
    return os.environ.get("DOIP_PUBLIC_URL", os.environ["DOIP_HOST"]).rstrip("/")


def _doip_url(run_id: str, component_id: str) -> str:
    """Build a DOIP retrieve URL for a components/-relative path."""
    element = component_id[len("components/"):]
    return f"{_doip_public_base()}/doip/retrieve/{run_id}/{element}"


def list_runs(lakefs_run_repo: str) -> list:
    """
    Find all runs in <lakefs_run_repo>/main by scanning for QID.fdo.json files
    at the sharded path pp/qq/rr/QNNN/QNNN.fdo.json.
    """
    client = _lakefs_client()
    repo   = lakefs.Repository(lakefs_run_repo, client=client)
    branch = repo.branch(LAKEFS_BRANCH)
    return [
        entry.path.split("/")[-1].removesuffix(".fdo.json")
        for entry in branch.objects()
        if entry.path.endswith(".fdo.json")
    ]


def _qid_from_uri(uri: str) -> str:
    """Extract a QID from a lakefs:// or DOIP retrieve URL.

    lakefs://repo/branch/.../QNNN/file  → first path segment starting with Q+digits
    https://.../doip/retrieve/QNNN/...  → segment immediately after /doip/retrieve/
    """
    if uri.startswith("lakefs://"):
        parts = uri[len("lakefs://"):].split("/")
        for part in parts:
            if part.upper().startswith("Q") and part[1:].isdigit():
                return part.upper()
        return ""
    marker = "/doip/retrieve/"
    idx = uri.find(marker)
    if idx != -1:
        after = uri[idx + len(marker):]
        qid_candidate = after.split("/")[0]
        if qid_candidate.upper().startswith("Q") and qid_candidate[1:].isdigit():
            return qid_candidate.upper()
    return ""


def _status_from_rocrate(rocrate_bytes: bytes) -> str:
    """Return 'success' or 'failed' by reading actionStatus from RO-Crate JSON."""
    if not rocrate_bytes:
        return ""
    try:
        crate = json.loads(rocrate_bytes)
        for entity in crate.get("@graph", []):
            action_status = entity.get("actionStatus", {})
            status_id = action_status.get("@id", "") if isinstance(action_status, dict) else str(action_status)
            if "Completed" in status_id:
                return "success"
            if "Failed" in status_id:
                return "failed"
    except Exception:
        pass
    return ""


def get_run_metadata(run_id: str, lakefs_run_repo: str) -> dict:
    """Read {run_id}.fdo.json and return a flat metadata dict including file lists."""
    client = _lakefs_client()
    repo   = lakefs.Repository(lakefs_run_repo, client=client)
    branch = repo.branch(LAKEFS_BRANCH)
    obj    = branch.object(f"{shard_qid(run_id)}/{run_id}.fdo.json")
    with obj.reader() as f:
        raw = f.read()
    fdo = json.loads(raw)

    qid = fdo.get("@id", "")
    if not qid:
        raise ValueError(f"{run_id}.fdo.json has no @id")
    if qid != run_id:
        raise ValueError(f"{run_id}.fdo.json @id '{qid}' does not match run_id")

    kernel     = fdo.get("kernel",     {})
    profile    = fdo.get("profile",    {})
    provenance = fdo.get("provenance", {})

    agent       = provenance.get("prov:wasAssociatedWith", {})
    attribution = agent.get("@id", "") if isinstance(agent, dict) else str(agent)
    docker_tag  = attribution.rsplit(":", 1)[-1] if ":" in attribution else ""

    model_name  = profile.get("name", "")
    model_image = profile.get("url",  "")
    if model_name and docker_tag and model_name == docker_tag:
        raise ValueError(f"{run_id}.fdo.json profile.name '{model_name}' looks like a docker tag")
    if model_image and "/" not in model_image:
        raise ValueError(f"{run_id}.fdo.json profile.url '{model_image}' is not a valid docker image URI")

    input_files, output_files = [], []
    for comp in kernel.get("fdo:hasComponent", []):
        comp_id = comp.get("@id", "")
        if comp_id.startswith("components/input/"):
            input_files.append(_doip_url(run_id, comp_id))
        elif comp_id.startswith("components/output/"):
            output_files.append(_doip_url(run_id, comp_id))

    input_dataset_qids = []
    data_transformation_sql = []
    for entry in provenance.get("prov:used", []):
        src_uri = entry.get("@id", "") if isinstance(entry, dict) else str(entry)
        qid_part = _qid_from_uri(src_uri)
        if qid_part and qid_part not in input_dataset_qids:
            input_dataset_qids.append(qid_part)
            data_transformation_sql.append(entry.get("schema:query", "") if isinstance(entry, dict) else "")

    try:
        rocrate_obj = branch.object(f"{shard_qid(run_id)}/components/ro-crate-metadata.json")
        with rocrate_obj.reader() as f:
            rocrate_bytes = f.read()
    except ObjectNotFoundException:
        rocrate_bytes = b""

    status = _status_from_rocrate(rocrate_bytes)

    return {
        "model_name":          model_name,
        "model_image":         model_image,
        "qid":                 qid,
        "docker_tag":          docker_tag,
        "run_timestamp":       kernel.get("modified",                  ""),
        "status":              status,
        "computation_time":    "",
        "fdo_bytes":           raw,
        "rocrate_bytes":       rocrate_bytes,
        "input_files":            input_files,
        "output_files":           output_files,
        "input_dataset_qids":     input_dataset_qids,
        "data_transformation_sql": data_transformation_sql,
    }


def mint_model_qid(docker_image: str) -> str:
    """Return a stable QID derived from the docker image URI (tag excluded)."""
    digest = hashlib.sha256(docker_image.encode()).hexdigest()
    return f"Q{int(digest, 16) % 10**13:013d}"


def _build_placeholder_model_fdo(
    qid: str,
    docker_image: str,
    model_name: str,
    docker_tag: str,
) -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "@context": [
            "https://w3id.org/fdo/context/v1",
            {
                "schema": "https://schema.org/",
                "prov": "http://www.w3.org/ns/prov#",
                "fdo": "https://w3id.org/fdo/vocabulary/",
            },
        ],
        "@id":   qid,
        "@type": "DigitalObject",
        "kernel": {
            "@id":               qid,
            "digitalObjectType": "https://schema.org/SoftwareApplication",
            "primaryIdentifier": qid,
            "kernelVersion":     "v1",
            "immutable":         False,
            "modified":          now,
        },
        "profile": {
            "@context":        "https://schema.org/",
            "@type":           "SoftwareApplication",
            "@id":             qid,
            "name":            model_name,
            "description":     f"Auto-created placeholder for model '{model_name}'.",
            "url":             docker_image,
            "softwareVersion": docker_tag,
        },
        "provenance": {
            "prov:generatedAtTime": now,
            "prov:wasAttributedTo": "EPISERVE Consortium sync-lakefs-ckan",
        },
    }


def ensure_model_fdo(
    docker_image: str,
    model_name: str,
    docker_tag: str,
    lakefs_models_repo: str,
    force: bool = False,
    log=print,
) -> str:
    """
    Ensure a model descriptor FDO exists in the lakeFS models repo.

    Checks the model's GitHub source repo for an existing fdo.json; if found,
    its fields are merged into the profile of the FDO. Otherwise a placeholder
    is written. The QID is always derived from docker_image — never taken from
    the repo file. Returns the stable model QID.
    """
    qid    = mint_model_qid(docker_image)
    path   = f"{shard_qid(qid)}/{qid}.fdo.json"
    client = _lakefs_client()
    branch = lakefs.Repository(lakefs_models_repo, client=client).branch(LAKEFS_BRANCH)

    if not force:
        try:
            with branch.object(path).reader() as f:
                existing_bytes = f.read()
            log(f"  model '{model_name}' ({qid}): already in lakeFS models repo, skipping.")
            return qid, existing_bytes
        except ObjectNotFoundException:
            pass

    fdo = _build_placeholder_model_fdo(qid, docker_image, model_name, docker_tag)

    if docker_image.startswith("ghcr.io/"):
        repo_path = docker_image[len("ghcr.io/"):]
        gh_url = f"https://raw.githubusercontent.com/{repo_path}/HEAD/fdo.json"
        log(f"  model '{model_name}' ({qid}): trying to get model fdo info from {gh_url}")
    else:
        gh_url = None

    repo_metadata = get_repo_fdo(docker_image)
    if repo_metadata is not None:
        log(f"  model '{model_name}' ({qid}): fdo.json found — merging profile metadata.")
        for key, value in repo_metadata.items():
            if key not in ("@id", "@context"):
                fdo["profile"][key] = value
    else:
        log(f"  model '{model_name}' ({qid}): no fdo.json found — writing placeholder.")

    fdo_bytes = json.dumps(fdo, indent=2).encode()
    # pre_sign=False: upload via the lakeFS API, not a presigned PUT. The SDK
    # omits Content-Type from the presigned SigV4 signature and Ceph RGW (Squid)
    # rejects the mismatch with 403 AccessDenied. Same fix as
    # workflow-prefect__dataset-downloader and workflow-prefect__model-runner.
    branch.object(path).upload(data=fdo_bytes, content_type="application/json", pre_sign=False)
    branch.commit(message=f"add model fdo for {qid}")
    log(f"  model '{model_name}' ({qid}): written to lakeFS models repo.")
    return qid, fdo_bytes


def list_models(lakefs_models_repo: str) -> list:
    """Return QIDs of all model descriptor FDOs in <lakefs_models_repo>/main."""
    client = _lakefs_client()
    repo   = lakefs.Repository(lakefs_models_repo, client=client)
    branch = repo.branch(LAKEFS_BRANCH)
    return [
        entry.path.split("/")[-1].removesuffix(".fdo.json")
        for entry in branch.objects()
        if entry.path.endswith(".fdo.json")
    ]


def get_model_metadata(model_qid: str, lakefs_models_repo: str) -> dict:
    """Read a model descriptor FDO and return a flat metadata dict."""
    client = _lakefs_client()
    branch = lakefs.Repository(lakefs_models_repo, client=client).branch(LAKEFS_BRANCH)
    path   = f"{shard_qid(model_qid)}/{model_qid}.fdo.json"
    with branch.object(path).reader() as f:
        raw = f.read()
    fdo = json.loads(raw)

    profile = fdo.get("profile", {})
    extras  = {e.get("key", ""): e.get("value", "") for e in fdo.get("extras", [])} if isinstance(fdo.get("extras"), list) else {}
    return {
        "name":                  profile.get("name",              ""),
        "description":           profile.get("description",       ""),
        "docker_image":          profile.get("url",               ""),
        "docker_tag":            profile.get("softwareVersion",   ""),
        "git_repo":              profile.get("codeRepository",    "") or extras.get("git_repo", ""),
        "algorithm":             extras.get("algorithm",          ""),
        "input_format":          extras.get("input_format",       ""),
        "output_format":         extras.get("output_format",      ""),
        "lead_researcher":       extras.get("lead_researcher",    ""),
        "domain":                extras.get("domain",             ""),
        "modality":              extras.get("modality",           ""),
        "paper_doi":             extras.get("paper_doi",          ""),
        "docker_image_created":  extras.get("docker_image_created", ""),
        "additional_properties": profile.get("additionalProperty", []),
        "fdo_bytes":             raw,
    }


def list_raw_datasets(lakefs_processed_repo: str) -> list:
    """Return paths of all .fdo.json files in <lakefs_processed_repo>/main."""
    client = _lakefs_client()
    repo   = lakefs.Repository(lakefs_processed_repo, client=client)
    branch = repo.branch(LAKEFS_BRANCH)
    return [
        entry.path
        for entry in branch.objects()
        if entry.path.endswith(".fdo.json")
    ]


def get_raw_dataset_metadata(fdo_path: str, lakefs_processed_repo: str) -> dict:
    """Read raw dataset FDO metadata.

    Args:
        fdo_path: Path to the raw dataset ``.fdo.json`` object in lakeFS.
        lakefs_processed_repo: lakeFS repository containing processed dataset
            FDO sidecars.

    Returns:
        dict: Flat metadata used to create or update the CKAN package,
        including component file entries.
    """
    client = _lakefs_client()
    repo   = lakefs.Repository(lakefs_processed_repo, client=client)
    branch = repo.branch(LAKEFS_BRANCH)
    obj    = branch.object(fdo_path)
    with obj.reader() as f:
        raw = f.read()
    fdo = json.loads(raw)

    qid        = fdo.get("@id",          "")
    kernel     = fdo.get("kernel",        {})
    profile    = fdo.get("profile",       {})
    provenance = fdo.get("provenance",    {})
    doip_base  = _doip_public_base()

    components = []
    for comp in kernel.get("fdo:hasComponent", []):
        comp_id    = comp.get("componentId", "")
        media_type = comp.get("mediaType",   "")
        if comp_id:
            components.append({
                "filename":   comp_id,
                "url":        f"{doip_base}/doip/retrieve/{qid}/{comp_id}",
                "media_type": media_type,
            })

    return {
        "qid":                 qid,
        "name":                profile.get("display_name", "") or profile.get("name", ""),
        "description":         profile.get("description",    ""),
        "source_url":          profile.get("url",            ""),
        "additional_type":     profile.get("additionalType", ""),
        "license_id":          profile.get("license",        ""),
        "attribution":         profile.get("creditText",     ""),
        "modified":            kernel.get("modified", "") or provenance.get("prov:generatedAtTime", ""),
        "source_changed_at":   provenance.get("source_changed_at", ""),
        "components":          components,
        "fdo_bytes":           raw,
    }
