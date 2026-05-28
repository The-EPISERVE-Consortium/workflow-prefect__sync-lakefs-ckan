"""
Prefect flow that periodically scans the lakeFS model-runs repository and
registers any new runs in CKAN. For each run folder found in lakeFS it checks
whether a CKAN dataset for that run_id already exists; if not, it creates one
and links all files from input/ and output/ as lakeFS URIs.
"""

from lakefs.exceptions import ObjectNotFoundException
from prefect import flow, task
from prefect.logging import get_run_logger

from flow.tools.ckan_tools import _ckan_run_exists, create_model_run
from flow.tools.lakefs_tools import get_run_metadata, list_run_files, list_runs


def _do_sync_run(run_id: str, lakefs_run_repo: str, log=print) -> None:
    if _ckan_run_exists(run_id):
        log(f"{run_id}: already in CKAN, skipping.")
        return

    try:
        metadata = get_run_metadata(run_id, lakefs_run_repo)
    except ObjectNotFoundException:
        log(f"{run_id}: no metadata.json, using empty defaults.")
        metadata = {}

    log(f"{run_id}: syncing...")
    input_files  = list_run_files(run_id, "input",  lakefs_run_repo)
    output_files = list_run_files(run_id, "output", lakefs_run_repo)

    create_model_run(
        model_name       = metadata.get("model_name",       ""),
        run_id           = run_id,
        git_commit       = metadata.get("git_commit",       ""),
        docker_tag       = metadata.get("docker_tag",       ""),
        run_timestamp    = metadata.get("run_timestamp",    ""),
        status           = metadata.get("status",           ""),
        computation_time = metadata.get("computation_time", ""),
        input_files      = input_files,
        output_files     = output_files,
    )
    log(f"{run_id}: done.")


@task
def sync_run(run_id: str, lakefs_run_repo: str) -> None:
    logger = get_run_logger()
    _do_sync_run(run_id, lakefs_run_repo, log=logger.info)


@flow
def sync_ckan_with_lakefs(lakefs_run_repo: str = "model-runs") -> None:
    """
    Scan the lakeFS model-runs repository and register any new runs in CKAN.
    Intended to run on a schedule as a Prefect deployment.
    """
    logger  = get_run_logger()
    run_ids = list_runs(lakefs_run_repo)
    logger.info(f"Found {len(run_ids)} runs in lakeFS")
    futures = [sync_run.submit(run_id, lakefs_run_repo) for run_id in run_ids]
    for future in futures:
        future.result()
