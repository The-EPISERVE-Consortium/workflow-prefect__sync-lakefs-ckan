"""
Prefect flow that periodically scans the lakeFS model-runs repository and
registers any new runs in CKAN. For each run folder found in lakeFS it checks
whether a CKAN dataset for that run_id already exists; if not, it creates one
and links all files from input/ and output/ as lakeFS URIs.
"""

from prefect import flow, task
from prefect.logging import get_run_logger

from ckan_tools import _ckan_run_exists, create_model_run
from lakefs_tools import get_run_metadata, list_run_files, list_runs


@task
def sync_run(run_id: str, lakefs_run_repo: str) -> None:
    logger = get_run_logger()

    if _ckan_run_exists(run_id):
        logger.info(f"Run {run_id} already in CKAN, skipping.")
        return

    logger.info(f"Syncing run {run_id} to CKAN")
    metadata     = get_run_metadata(run_id, lakefs_run_repo)
    input_files  = list_run_files(run_id, "input", lakefs_run_repo)
    output_files = list_run_files(run_id, "output", lakefs_run_repo)

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
