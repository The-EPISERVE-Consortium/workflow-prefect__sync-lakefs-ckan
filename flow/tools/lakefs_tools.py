"""lakeFS helper functions."""

import json
import os

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
        for entry in branch.objects.list(delimiter="/")
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
        for entry in branch.objects.list(prefix=prefix)
    ]


def get_run_metadata(run_id: str, lakefs_run_repo: str) -> dict:
    """Read and return the parsed metadata.json for a given run."""
    client = _lakefs_client()
    repo   = lakefs.Repository(lakefs_run_repo, client=client)
    branch = repo.branch(LAKEFS_BRANCH)
    obj    = branch.object(f"{run_id}/metadata.json")
    with obj.reader() as f:
        return json.loads(f.read())
