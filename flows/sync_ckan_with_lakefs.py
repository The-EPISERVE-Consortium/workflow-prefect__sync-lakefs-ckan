"""
Prefect flow that periodically scans the lakeFS model-runs repository and
registers any new runs in CKAN. For each run folder found in lakeFS it checks
whether a CKAN dataset for that run_id already exists; if not, it creates one
and links all files from input/ and output/ as lakeFS URIs.
"""

import json
import os

import lakefs
import requests
from prefect import flow, task
from prefect.logging import get_run_logger

# ── Config ─────────────────────────────────────────────────────────────────────

CKAN_URL        = os.environ["CKAN_HOST"]
LAKEFS_RUN_REPO = "model-runs"
LAKEFS_BRANCH   = "main"


# ── CKAN helpers ───────────────────────────────────────────────────────────────

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


# ── CKAN create functions ──────────────────────────────────────────────────────

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
    domain: str,
    modality: str,
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
            _vtag(vocabs, "domain",   domain),
            _vtag(vocabs, "status",   status),
            _vtag(vocabs, "modality", modality),
        ],
        "extras": [
            {"key": "run_id",        "value": run_id},
            {"key": "model",         "value": model_name},
            {"key": "git_commit",    "value": git_commit},
            {"key": "docker_tag",    "value": docker_tag},
            {"key": "run_timestamp", "value": run_timestamp},
            {"key": "status",        "value": status},
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


# ── lakeFS helpers ─────────────────────────────────────────────────────────────

def _lakefs_client() -> lakefs.client.Client:
    return lakefs.client.Client(
        host=os.environ["LAKEFS_HOST"],
        username=os.environ["LAKEFS_ACCESS_KEY"],
        password=os.environ["LAKEFS_SECRET_KEY"],
    )


def _list_lakefs_runs() -> list:
    """
    List all top-level run folders in model-runs/main.
    Returns a list of run_id strings (bare files at root are skipped).
    """
    client = _lakefs_client()
    repo   = lakefs.Repository(LAKEFS_RUN_REPO, client=client)
    branch = repo.branch(LAKEFS_BRANCH)
    return [
        entry.path.rstrip("/")
        for entry in branch.objects.list(delimiter="/")
        if "/" in entry.path
    ]


def _list_run_files(run_id: str, subdir: str) -> list:
    """
    List all objects under <run_id>/<subdir>/ and return full lakeFS URIs.
    """
    client = _lakefs_client()
    repo   = lakefs.Repository(LAKEFS_RUN_REPO, client=client)
    branch = repo.branch(LAKEFS_BRANCH)
    prefix = f"{run_id}/{subdir}/"
    return [
        f"lakefs://{LAKEFS_RUN_REPO}/{LAKEFS_BRANCH}/{entry.path}"
        for entry in branch.objects.list(prefix=prefix)
    ]


def _get_run_metadata(run_id: str) -> dict:
    """Read and return the parsed metadata.json for a given run."""
    client = _lakefs_client()
    repo   = lakefs.Repository(LAKEFS_RUN_REPO, client=client)
    branch = repo.branch(LAKEFS_BRANCH)
    obj    = branch.object(f"{run_id}/metadata.json")
    with obj.reader() as f:
        return json.loads(f.read())


# ── CKAN helpers ───────────────────────────────────────────────────────────────

def _ckan_run_exists(run_id: str) -> bool:
    """Return True if a CKAN dataset with extras_run_id == run_id already exists."""
    r = requests.get(
        f"{CKAN_URL}/api/3/action/package_search",
        params={"q": f"extras_run_id:{run_id}", "rows": 1},
    )
    return r.json()["result"]["count"] > 0


# ── Prefect tasks & flow ───────────────────────────────────────────────────────

@task
def sync_run(run_id: str) -> None:
    logger = get_run_logger()

    if _ckan_run_exists(run_id):
        logger.info(f"Run {run_id} already in CKAN, skipping.")
        return

    logger.info(f"Syncing run {run_id} to CKAN")
    metadata     = _get_run_metadata(run_id)
    input_files  = _list_run_files(run_id, "input")
    output_files = _list_run_files(run_id, "output")

    create_model_run(
        model_name    = metadata["model_name"],
        run_id        = run_id,
        git_commit    = metadata["git_commit"],
        docker_tag    = metadata["docker_tag"],
        run_timestamp = metadata["run_timestamp"],
        status        = metadata["status"],
        domain        = metadata["domain"],
        modality      = metadata["modality"],
        input_files   = input_files,
        output_files  = output_files,
    )
    logger.info(f"Run {run_id} synced to CKAN successfully")


@flow
def sync_ckan_with_lakefs() -> None:
    """
    Scan the lakeFS model-runs repository and register any new runs in CKAN.
    Intended to run on a schedule as a Prefect deployment.
    """
    logger  = get_run_logger()
    run_ids = _list_lakefs_runs()
    logger.info(f"Found {len(run_ids)} runs in lakeFS")
    futures = [sync_run.submit(run_id) for run_id in run_ids]
    for future in futures:
        future.result()
