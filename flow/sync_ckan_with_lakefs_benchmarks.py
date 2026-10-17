"""
Prefect flow that registers the generated files of benchmarks in CKAN.

The API server stores the input and the reference file of every finalized
benchmark as FDO objects in the lakeFS repository ``benchmarks`` (one QID and
one ``.fdo.json`` each). This flow reads those sidecars and creates a CKAN
dataset per object in the group ``type-benchmark-data``, so the files show up
in the catalog without being offered as source datasets of new benchmarks.
The objects never change, so an object that already exists in CKAN is skipped.
"""

from prefect import flow, task
from prefect.logging import get_run_logger

from tools.ckan_tools import _ckan_raw_dataset_exists, create_raw_dataset
from tools.lakefs_tools import get_raw_dataset_metadata, list_raw_datasets

GROUP = "type-benchmark-data"
DATASET_TYPE = "benchmark-data"


def benchmark_extras(profile: dict) -> list:
    """CKAN extras describing a benchmark object (``profile.episerveBenchmark`` of its FDO)."""
    info = profile.get("episerveBenchmark", {}) or {}
    extras = [
        {"key": "benchmark_id",     "value": str(info.get("id", ""))},
        {"key": "benchmark_role",   "value": info.get("role", "")},
        {"key": "source_qids",      "value": ",".join(info.get("source_qids", []))},
        {"key": "data_commit",      "value": info.get("data_commit") or ""},
        {"key": "sha256",           "value": info.get("sha256", "")},
    ]
    return [e for e in extras if e["value"]]


def _do_sync_benchmark_object(fdo_path: str, lakefs_benchmarks_repo: str, log=print) -> None:
    try:
        metadata = get_raw_dataset_metadata(fdo_path, lakefs_benchmarks_repo)
    except Exception as e:
        log(f"{fdo_path}: skipping ({e})")
        return
    qid = metadata["qid"]
    if not qid:
        log(f"{fdo_path}: no QID, skipping.")
        return
    if _ckan_raw_dataset_exists(qid):
        log(f"{qid}: already in CKAN, skipping.")
        return
    log(f"{qid}: syncing...")
    create_raw_dataset(
        qid             = qid,
        name            = metadata["name"],
        description     = metadata["description"],
        source_url      = metadata["source_url"],
        modified        = metadata["modified"],
        components      = metadata["components"],
        fdo_bytes       = metadata["fdo_bytes"],
        additional_type = metadata.get("additional_type", ""),
        access          = metadata.get("access", "") or "open",
        group           = GROUP,
        dataset_type    = DATASET_TYPE,
        extra_extras    = benchmark_extras(metadata.get("profile", {})),
    )
    log(f"{qid}: done.")


@task
def sync_benchmark_object(fdo_path: str, lakefs_benchmarks_repo: str) -> None:
    _do_sync_benchmark_object(fdo_path, lakefs_benchmarks_repo, log=get_run_logger().info)


@flow
def sync_ckan_with_lakefs_benchmarks(lakefs_benchmarks_repo: str = "benchmarks") -> None:
    """Register every benchmark object of the lakeFS repository in CKAN (new ones only)."""
    logger    = get_run_logger()
    fdo_paths = list_raw_datasets(lakefs_benchmarks_repo)
    logger.info(f"Found {len(fdo_paths)} benchmark objects in lakeFS")
    futures = [sync_benchmark_object.submit(path, lakefs_benchmarks_repo) for path in fdo_paths]
    for future in futures:
        future.result()
