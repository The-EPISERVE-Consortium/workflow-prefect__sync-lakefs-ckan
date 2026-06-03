"""
Prefect flow that periodically scans the lakeFS data-processed repository and
registers any new datasets in CKAN. Each dataset has an .fdo.json sidecar
file that contains the metadata. The flow reads the FDO metadata, uploads
it to CKAN, and registers all data files as resources.
"""

from prefect import flow, task
from prefect.logging import get_run_logger

from tools.ckan_tools import _ckan_delete_raw_dataset, _ckan_raw_dataset_exists, create_raw_dataset
from tools.lakefs_tools import get_raw_dataset_metadata, list_raw_datasets


def _do_sync_raw_dataset(fdo_path: str, lakefs_processed_repo: str, log=print, force_recreate: bool = False) -> None:
    try:
        metadata = get_raw_dataset_metadata(fdo_path, lakefs_processed_repo)
    except Exception as e:
        log(f"{fdo_path}: skipping ({e})")
        return

    qid = metadata["qid"]
    if not qid:
        log(f"{fdo_path}: no QID, skipping.")
        return

    if _ckan_raw_dataset_exists(qid):
        if not force_recreate:
            log(f"{qid}: already in CKAN, skipping.")
            return
        log(f"{qid}: already in CKAN, overwriting.")
        _ckan_delete_raw_dataset(qid)

    log(f"{qid}: syncing...")
    create_raw_dataset(
        qid         = qid,
        name        = metadata["name"],
        description = metadata["description"],
        source_url  = metadata["source_url"],
        modified    = metadata["modified"],
        components  = metadata["components"],
        fdo_bytes   = metadata["fdo_bytes"],
    )
    log(f"{qid}: done.")


@task
def sync_raw_dataset(fdo_path: str, lakefs_processed_repo: str, force_recreate: bool = False) -> None:
    logger = get_run_logger()
    _do_sync_raw_dataset(fdo_path, lakefs_processed_repo, log=logger.info, force_recreate=force_recreate)


@flow
def sync_ckan_with_lakefs_dataprocessed(lakefs_processed_repo: str = "data-processed", force_recreate: bool = False) -> None:
    """
    Scan the lakeFS data-processed repository and register any new datasets in CKAN.
    Intended to run on a schedule as a Prefect deployment.
    """
    logger    = get_run_logger()
    fdo_paths = list_raw_datasets(lakefs_processed_repo)
    logger.info(f"Found {len(fdo_paths)} datasets in lakeFS")
    futures = [sync_raw_dataset.submit(fdo_path, lakefs_processed_repo, force_recreate) for fdo_path in fdo_paths]
    for future in futures:
        future.result()
