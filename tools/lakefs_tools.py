"""lakeFS helper functions."""

import json
import os
from urllib.parse import quote

import lakefs
from lakefs.exceptions import ObjectNotFoundException

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


def _doip_url(run_id: str, component_id: str) -> str:
    """Build a DOIP retrieve URL for a components/-relative path."""
    element = component_id[len("components/"):]
    return f"{os.environ['DOIP_HOST']}/doip/retrieve/{run_id}/{element}"


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

    kernel     = fdo.get("kernel",     {})
    profile    = fdo.get("profile",    {})
    provenance = fdo.get("provenance", {})

    attribution = provenance.get("prov:wasAttributedTo", "")
    docker_tag  = attribution.rsplit(":", 1)[-1] if ":" in attribution else ""

    input_files, output_files = [], []
    for comp in kernel.get("fdo:hasComponent", []):
        comp_id = comp.get("@id", "")
        if comp_id.startswith("components/input/"):
            input_files.append(_doip_url(run_id, comp_id))
        elif comp_id.startswith("components/output/"):
            output_files.append(_doip_url(run_id, comp_id))

    try:
        rocrate_obj = branch.object(f"{shard_qid(run_id)}/components/ro-crate-metadata.json")
        with rocrate_obj.reader() as f:
            rocrate_bytes = f.read()
    except ObjectNotFoundException:
        rocrate_bytes = b""

    return {
        "model_name":       profile.get("name",                     ""),
        "model_image":      profile.get("url",                      ""),
        "qid":              qid,
        "docker_tag":       docker_tag,
        "run_timestamp":    kernel.get("modified",                  ""),
        "status":           "",
        "computation_time": "",
        "fdo_bytes":        raw,
        "rocrate_bytes":    rocrate_bytes,
        "input_files":      input_files,
        "output_files":     output_files,
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
    """Read .fdo.json and return a flat metadata dict including component file list."""
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
    doip_base  = os.environ["DOIP_HOST"]

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
        "qid":         qid,
        "name":        profile.get("name",                     ""),
        "description": profile.get("description",             ""),
        "source_url":  profile.get("url",                     ""),
        "modified":    provenance.get("prov:generatedAtTime", ""),
        "components":  components,
        "fdo_bytes":   raw,
    }
