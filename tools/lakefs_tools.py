"""lakeFS helper functions."""

import json
import os
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

import lakefs

from tools.sharding import shard_qid

LAKEFS_BRANCH = "main"


def _lakefs_client() -> lakefs.client.Client:
    return lakefs.client.Client(
        host=os.environ["LAKEFS_HOST"],
        username=os.environ["LAKEFS_ACCESS_KEY"],
        password=os.environ["LAKEFS_SECRET_KEY"],
    )


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
