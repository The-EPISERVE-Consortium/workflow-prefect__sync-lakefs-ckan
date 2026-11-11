"""Register generated benchmark files and reconcile portable definition resources.

Existing benchmark packages retain their data resources and administrative views.
Definitions added to their FDO later are synchronized without recreating the package.
"""

from prefect import flow, task
from prefect.logging import get_run_logger

from tools.ckan_tools import _ckan_raw_dataset_exists, create_raw_dataset, sync_benchmark_definition
from tools.lakefs_tools import get_raw_dataset_metadata, list_raw_datasets

GROUP = "type-benchmark-data"
DATASET_TYPE = "benchmark-data"


def benchmark_extras(profile: dict) -> list:
    """CKAN extras describing a benchmark object (``profile.episerveBenchmark`` of its FDO)."""
    info = profile.get("episerveBenchmark", {}) or {}
    extras = [
        {"key": "benchmark_id",     "value": str(info.get("id", ""))},
        {"key": "source_qids",      "value": ",".join(info.get("source_qids", []))},
        {"key": "data_commit",      "value": info.get("data_commit") or ""},
    ]
    return [e for e in extras if e["value"]]


def _do_sync_benchmark_object(fdo_path: str, lakefs_benchmarks_repo: str, log=print) -> None:
    """Register new benchmark objects and add missing definitions to existing ones.

    Args:
        fdo_path: lakeFS path of the benchmark FDO.
        lakefs_benchmarks_repo: Repository containing frozen benchmark objects.
        log: Progress logging callback.
    """
    try:
        metadata = get_raw_dataset_metadata(fdo_path, lakefs_benchmarks_repo, pin_components=True)
    except Exception as e:
        log(f"{fdo_path}: skipping ({e})")
        return
    qid = metadata["qid"]
    if not qid:
        log(f"{fdo_path}: no QID, skipping.")
        return
    definition = ((metadata.get("profile", {}).get("episerveBenchmark") or {}).get("files") or {}).get("definition") or {}
    components = [dict(item) for item in metadata["components"]]
    for item in components:
        if item["filename"] == "benchmark-definition.json":
            item["sha256"] = definition.get("sha256", "")
    if _ckan_raw_dataset_exists(qid):
        changed = sync_benchmark_definition(qid, components)
        log(f"{qid}: definition {'updated' if changed else 'already synchronized'}.")
        return
    log(f"{qid}: syncing...")
    pkg = create_raw_dataset(
        qid             = qid,
        name            = metadata["name"],
        description     = metadata["description"],
        source_url      = metadata["source_url"],
        modified        = metadata["modified"],
        components      = [item for item in components if item["filename"] != "benchmark-definition.json"],
        fdo_bytes       = metadata["fdo_bytes"],
        additional_type = metadata.get("additional_type", ""),
        access          = metadata.get("access", "") or "open",
        group           = GROUP,
        dataset_type    = DATASET_TYPE,
        extra_extras    = benchmark_extras(metadata.get("profile", {})),
    )
    sync_benchmark_definition(qid, components, package=pkg)
    log(f"{qid}: done.")


@task
def sync_benchmark_object(fdo_path: str, lakefs_benchmarks_repo: str) -> None:
    """Synchronize one benchmark object as a Prefect task.

    Args:
        fdo_path: lakeFS path of the benchmark FDO.
        lakefs_benchmarks_repo: Repository containing benchmark objects.
    """
    _do_sync_benchmark_object(fdo_path, lakefs_benchmarks_repo, log=get_run_logger().info)


@flow
def sync_ckan_with_lakefs_benchmarks(lakefs_benchmarks_repo: str = "benchmarks") -> None:
    """Register benchmark objects and reconcile definition resources in CKAN.

    Args:
        lakefs_benchmarks_repo: Repository containing frozen benchmark objects.
    """
    logger    = get_run_logger()
    fdo_paths = list_raw_datasets(lakefs_benchmarks_repo)
    logger.info(f"Found {len(fdo_paths)} benchmark objects in lakeFS")
    futures = [sync_benchmark_object.submit(path, lakefs_benchmarks_repo) for path in fdo_paths]
    for future in futures:
        future.result()
