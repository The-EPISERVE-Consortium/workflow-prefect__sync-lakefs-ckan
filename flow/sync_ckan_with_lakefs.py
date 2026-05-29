"""
Prefect flow that periodically scans the lakeFS model-runs repository and
registers any new runs in CKAN. Each run is stored under its QID path in
lakeFS (e.g. Q1748526042817/). The flow reads the RO-Crate metadata,
uploads it to CKAN, and registers all input/output files as resources.
"""

from lakefs.exceptions import ObjectNotFoundException
from prefect import flow, task
from prefect.logging import get_run_logger

from tools.ckan_tools import _ckan_delete_run, _ckan_run_exists, create_model_run
from tools.lakefs_tools import get_run_metadata, list_runs


def _do_sync_run(run_id: str, lakefs_run_repo: str, log=print, force_recreate: bool = False) -> None:
    if _ckan_run_exists(run_id):
        if not force_recreate:
            log(f"{run_id}: already in CKAN, skipping.")
            return
        log(f"{run_id}: already in CKAN, overwriting.")
        _ckan_delete_run(run_id)

    try:
        metadata = get_run_metadata(run_id, lakefs_run_repo)
    except ValueError:
        log(f"{run_id}: not a valid QID, skipping.")
        return
    except ObjectNotFoundException:
        log(f"{run_id}: no ro-crate-metadata.json, skipping.")
        return

    log(f"{run_id}: syncing...")
    create_model_run(
        model_name       = metadata.get("model_name",       ""),
        run_id           = run_id,
        qid              = metadata.get("qid",              ""),
        git_commit       = metadata.get("git_commit",       ""),
        docker_tag       = metadata.get("docker_tag",       ""),
        run_timestamp    = metadata.get("run_timestamp",    ""),
        status           = metadata.get("status",           ""),
        computation_time = metadata.get("computation_time", ""),
        rocrate_bytes    = metadata.get("rocrate_bytes",    b""),
        input_files      = metadata.get("input_files",  []),
        output_files     = metadata.get("output_files", []),
    )
    log(f"{run_id}: done.")


@task
def sync_run(run_id: str, lakefs_run_repo: str, force_recreate: bool = False) -> None:
    logger = get_run_logger()
    _do_sync_run(run_id, lakefs_run_repo, log=logger.info, force_recreate=force_recreate)


@flow
def sync_ckan_with_lakefs(lakefs_run_repo: str = "model-runs", force_recreate: bool = False) -> None:
    """
    Scan the lakeFS model-runs repository and register any new runs in CKAN.
    Intended to run on a schedule as a Prefect deployment.
    """
    logger  = get_run_logger()
    run_ids = list_runs(lakefs_run_repo)
    logger.info(f"Found {len(run_ids)} runs in lakeFS")
    futures = [sync_run.submit(run_id, lakefs_run_repo, force_recreate) for run_id in run_ids]
    for future in futures:
        future.result()
