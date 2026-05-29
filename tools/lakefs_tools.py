"""lakeFS helper functions."""

import json
import os
from urllib.parse import quote

import lakefs

LAKEFS_BRANCH = "main"


def lakefs_uri_to_http(uri: str) -> str:
    """
    Convert a lakefs:// URI to the lakeFS HTTP API object URL.

    lakefs://model-runs/main/run-id/input/file.json
    → https://<LAKEFS_HOST>/api/v1/repositories/model-runs/refs/main/objects
      ?path=run-id%2Finput%2Ffile.json&presign=false
    """
    # strip scheme
    without_scheme = uri[len("lakefs://"):]
    repo, branch, *parts = without_scheme.split("/")
    path = "/".join(parts)
    host = os.environ["LAKEFS_HOST"].rstrip("/")
    return (
        f"{host}/api/v1/repositories/{repo}/refs/{branch}/objects"
        f"?path={quote(path, safe='')}&presign=false"
    )


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


def list_run_files(run_id: str, subdir: str, lakefs_run_repo: str) -> list:
    """
    List all objects under <run_id>/<subdir>/ and return full lakeFS URIs.
    """
    client = _lakefs_client()
    repo   = lakefs.Repository(lakefs_run_repo, client=client)
    branch = repo.branch(LAKEFS_BRANCH)
    prefix = f"{run_id}/{subdir}/"
    return [
        f"lakefs://{lakefs_run_repo}/{LAKEFS_BRANCH}/{entry.path}"
        for entry in branch.objects(prefix=prefix)
    ]


def get_run_metadata(run_id: str, lakefs_run_repo: str) -> dict:
    """Read ro-crate-metadata.json and return a flat metadata dict."""
    client = _lakefs_client()
    repo   = lakefs.Repository(lakefs_run_repo, client=client)
    branch = repo.branch(LAKEFS_BRANCH)
    obj    = branch.object(f"{run_id}/ro-crate-metadata.json")
    with obj.reader() as f:
        crate = json.loads(f.read())
    dataset = next(e for e in crate["@graph"] if e.get("@id") == "./")
    return {
        "model_name":       dataset.get("name",             ""),
        "git_commit":       dataset.get("git_commit",       ""),
        "docker_tag":       dataset.get("docker_tag",       ""),
        "run_timestamp":    dataset.get("datePublished",    ""),
        "status":           dataset.get("status",           ""),
        "computation_time": dataset.get("computation_time", ""),
    }
