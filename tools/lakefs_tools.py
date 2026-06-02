"""lakeFS helper functions."""

import json
import os
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, unquote, urlparse

import lakefs

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
    Find all runs in <lakefs_run_repo>/main by scanning for ro-crate-metadata.json
    files and extracting the QID from the sharded path pp/qq/rr/QNNN/ro-crate-metadata.json.
    """
    client = _lakefs_client()
    repo   = lakefs.Repository(lakefs_run_repo, client=client)
    branch = repo.branch(LAKEFS_BRANCH)
    return [
        entry.path.split("/")[-2]
        for entry in branch.objects()
        if entry.path.endswith("/ro-crate-metadata.json")
    ]


def get_run_metadata(run_id: str, lakefs_run_repo: str) -> dict:
    """Read ro-crate-metadata.json and return a flat metadata dict including file lists."""
    client = _lakefs_client()
    repo   = lakefs.Repository(lakefs_run_repo, client=client)
    branch = repo.branch(LAKEFS_BRANCH)
    obj    = branch.object(f"{shard_qid(run_id)}/ro-crate-metadata.json")
    with obj.reader() as f:
        raw = f.read()
    crate = json.loads(raw)
    graph = crate.get("@graph")
    if not graph:
        raise ValueError(f"ro-crate-metadata.json for {run_id} has no @graph")
    dataset = next((e for e in graph if e.get("@id") == "./"), None)
    if dataset is None:
        raise ValueError(f"ro-crate-metadata.json for {run_id} has no root Dataset entry")
    action  = next((e for e in graph if e.get("@type") == "CreateAction"), None)
    sw      = next((e for e in graph if e.get("@type") == "SoftwareApplication"), None)

    status_id = (action or {}).get("actionStatus", {}).get("@id", "")
    status    = (
        "success" if status_id.endswith("CompletedActionStatus") else
        "failed"  if status_id.endswith("FailedActionStatus")   else ""
    )

    docker_tag = (sw or {}).get("softwareVersion", "")

    start = (action or {}).get("startTime")
    end   = (action or {}).get("endTime")
    if start and end:
        computation_time = int(
            (datetime.fromisoformat(end.replace("Z", "+00:00")) -
             datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds()
        )
    else:
        computation_time = ""

    input_files, output_files = [], []
    for part in dataset.get("hasPart", []):
        part_id = part["@id"]
        if part_id.startswith("components/"):
            url = _doip_url(run_id, part_id)
            if part_id.startswith("components/input/"):
                input_files.append(url)
            elif part_id.startswith("components/output/"):
                output_files.append(url)
        else:
            # Legacy format: full lakeFS API URL already in @id
            path = unquote(parse_qs(urlparse(part_id).query).get("path", [""])[0])
            if f"{run_id}/input/" in path:
                input_files.append(part_id)
            elif f"{run_id}/output/" in path:
                output_files.append(part_id)

    return {
        "model_name":       dataset.get("name",          ""),
        "qid":              dataset.get("identifier",    ""),
        "docker_tag":       docker_tag,
        "run_timestamp":    dataset.get("datePublished", ""),
        "status":           status,
        "computation_time": computation_time,
        "rocrate_bytes":    raw,
        "input_files":      input_files,
        "output_files":     output_files,
    }


def list_raw_datasets(lakefs_raw_repo: str) -> list:
    """Return paths of all .fdo.json files in <lakefs_raw_repo>/main."""
    client = _lakefs_client()
    repo   = lakefs.Repository(lakefs_raw_repo, client=client)
    branch = repo.branch(LAKEFS_BRANCH)
    return [
        entry.path
        for entry in branch.objects()
        if entry.path.endswith(".fdo.json")
    ]


def get_raw_dataset_metadata(fdo_path: str, lakefs_raw_repo: str) -> dict:
    """Read .fdo.json and return a flat metadata dict including component file list."""
    client = _lakefs_client()
    repo   = lakefs.Repository(lakefs_raw_repo, client=client)
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
