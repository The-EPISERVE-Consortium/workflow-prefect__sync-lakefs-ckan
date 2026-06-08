"""
Prefect flow that periodically scans the lakeFS models repository and
registers any new model descriptor FDOs in CKAN.
"""

from prefect import flow, task
from prefect.logging import get_run_logger

from tools.ckan_tools import create_model
from tools.lakefs_tools import get_model_metadata, list_models


def _do_sync_model(model_qid: str, lakefs_models_repo: str, log=print, force_recreate: bool = False) -> None:
    try:
        meta = get_model_metadata(model_qid, lakefs_models_repo)
    except Exception as e:
        log(f"{model_qid}: skipping — {e}")
        return

    log(f"{model_qid}: syncing to CKAN...")
    create_model(
        name                 = meta.get("name",            ""),
        description          = meta.get("description",     ""),
        git_repo             = meta.get("git_repo",        ""),
        docker_image         = meta.get("docker_image",    ""),
        docker_tag           = meta.get("docker_tag",      ""),
        docker_image_created = meta.get("docker_image_created", ""),
        model_qid            = model_qid,
        algorithm            = meta.get("algorithm",       ""),
        input_format         = meta.get("input_format",    ""),
        output_format        = meta.get("output_format",   ""),
        lead_researcher      = meta.get("lead_researcher", ""),
        domain               = meta.get("domain",          ""),
        modality             = meta.get("modality",        ""),
        paper_doi            = meta.get("paper_doi",       ""),
        force_recreate       = force_recreate,
    )
    log(f"{model_qid}: done.")


@task
def sync_model(model_qid: str, lakefs_models_repo: str, force_recreate: bool = False) -> None:
    logger = get_run_logger()
    _do_sync_model(model_qid, lakefs_models_repo, log=logger.info, force_recreate=force_recreate)


@flow
def sync_ckan_with_lakefs_models(lakefs_models_repo: str = "models", force_recreate: bool = False) -> None:
    """
    Scan the lakeFS models repository and register any new model descriptors in CKAN.
    Intended to run on a schedule as a Prefect deployment.
    """
    logger     = get_run_logger()
    model_qids = list_models(lakefs_models_repo)
    logger.info(f"Found {len(model_qids)} models in lakeFS")
    futures = [sync_model.submit(qid, lakefs_models_repo, force_recreate) for qid in model_qids]
    for future in futures:
        future.result()
