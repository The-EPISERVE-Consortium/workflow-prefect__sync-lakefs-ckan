"""
Prefect flow that periodically scans the lakeFS model-runs repository and
registers any new runs in CKAN. Each run is stored under its QID path in
lakeFS (e.g. Q1748526042817/). The flow reads the RO-Crate metadata,
uploads it to CKAN, and registers all input/output files as resources.
"""

import os

from lakefs.exceptions import ObjectNotFoundException
from prefect import flow, task
from prefect.logging import get_run_logger

from tools.ckan_tools import _ckan_delete_run, _ckan_run_exists, create_model_run, ensure_model
from tools.lakefs_tools import ensure_model_fdo, get_run_metadata, list_runs


def _do_sync_run(run_id: str, lakefs_run_repo: str, log=print, force_recreate: bool = False) -> None:
    if _ckan_run_exists(run_id):
        if not force_recreate:
            log(f"{run_id}: already in CKAN, skipping.")
            return
        log(f"{run_id}: already in CKAN, overwriting.")
        _ckan_delete_run(run_id)

    try:
        metadata = get_run_metadata(run_id, lakefs_run_repo)
    except ValueError as e:
        log(f"{run_id}: skipping — {e}")
        return
    except ObjectNotFoundException:
        log(f"{run_id}: no FDO metadata file, skipping.")
        return

    log(f"{run_id}: syncing...")
    model_name  = metadata.get("model_name",  "")
    model_image = metadata.get("model_image", "")
    docker_tag  = metadata.get("docker_tag",  "")
    model_qid      = ""
    model_fdo_bytes = b""
    if model_name and model_image:
        log(f"{run_id}: ensuring model FDO for '{model_name}' ({model_image})")
        lakefs_models_repo = os.environ.get("LAKEFS_MODELS_REPO", "models")
        model_qid, model_fdo_bytes = ensure_model_fdo(
            docker_image       = model_image,
            model_name         = model_name,
            docker_tag         = docker_tag,
            lakefs_models_repo = lakefs_models_repo,
            force              = force_recreate,
            log                = log,
        )
        log(f"{run_id}: model QID → {model_qid}")
    if model_name:
        ensure_model(
            model_name     = model_name,
            docker_image   = model_image,
            docker_tag     = docker_tag,
            model_qid      = model_qid,
            fdo_bytes      = model_fdo_bytes,
            force_recreate = force_recreate,
        )
    create_model_run(
        model_name       = model_name,
        run_id           = run_id,
        qid              = metadata.get("qid",              ""),
        docker_tag       = docker_tag,
        run_timestamp    = metadata.get("run_timestamp",    ""),
        status           = metadata.get("status",           ""),
        computation_time = metadata.get("computation_time", ""),
        fdo_bytes        = metadata.get("fdo_bytes",      b""),
        rocrate_bytes    = metadata.get("rocrate_bytes", b""),
        input_files      = metadata.get("input_files",  []),
        output_files     = metadata.get("output_files", []),
        model_qid        = model_qid,
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
