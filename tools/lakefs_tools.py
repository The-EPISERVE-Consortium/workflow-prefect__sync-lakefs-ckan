"""lakeFS helper functions."""

import json
import os
from urllib.parse import parse_qs, unquote, urlparse

import lakefs

LAKEFS_BRANCH = "main"



def _lakefs_client() -> lakefs.client.Client:
    return lakefs.client.Client(
        host=os.environ["LAKEFS_HOST"],
        username=os.environ["LAKEFS_ACCESS_KEY"],
        password=os.environ["LAKEFS_SECRET_KEY"],
    )


def list_runs(lakefs_run_repo: str) -> list:
    """
    List all top-level run folders in <lakefs_run_repo>/main.
    Returns a list of run_id strings (bare files at root are skipped).
    """
    client = _lakefs_client()
    repo   = lakefs.Repository(lakefs_run_repo, client=client)
    branch = repo.branch(LAKEFS_BRANCH)
    return [
        entry.path.rstrip("/")
        for entry in branch.objects(delimiter="/")
        if "/" in entry.path
    ]


def get_run_metadata(run_id: str, lakefs_run_repo: str) -> dict:
    """Read ro-crate-metadata.json and return a flat metadata dict including file lists."""
    client = _lakefs_client()
    repo   = lakefs.Repository(lakefs_run_repo, client=client)
    branch = repo.branch(LAKEFS_BRANCH)
    obj    = branch.object(f"{run_id}/ro-crate-metadata.json")
    with obj.reader() as f:
        raw = f.read()
    crate   = json.loads(raw)
    dataset = next(e for e in crate["@graph"] if e.get("@id") == "./")

    input_files, output_files = [], []
    for part in dataset.get("hasPart", []):
        url  = part["@id"]
        path = unquote(parse_qs(urlparse(url).query).get("path", [""])[0])
        if f"{run_id}/input/" in path:
            input_files.append(url)
        elif f"{run_id}/output/" in path:
            output_files.append(url)

    return {
        "model_name":       dataset.get("name",             ""),
        "git_commit":       dataset.get("git_commit",       ""),
        "docker_tag":       dataset.get("docker_tag",       ""),
        "run_timestamp":    dataset.get("datePublished",    ""),
        "status":           dataset.get("status",           ""),
        "computation_time": dataset.get("computation_time", ""),
        "rocrate_bytes":    raw,
        "input_files":      input_files,
        "output_files":     output_files,
    }
