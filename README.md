# workflow-prefect__sync-lakefs-ckan

Prefect flows that keep the CKAN data catalog in sync with lakeFS: processed datasets (`data-processed`), model runs (`model-runs`), models (`models`) and generated benchmark files (`benchmarks`). All are intended to run on a schedule.

## Flows

### `sync_ckan_with_lakefs` — model runs

**File:** `flow/sync_ckan_with_lakefs_modelruns.py`  
**Deployment:** `sync-ckan-with-lakefs-modelruns` (no schedule; a Prefect automation starts it after every completed `model-runner` run)

Scans `model-runs` in lakeFS for QID folders, reads the FDO metadata (`<QID>.fdo.json`) and RO-Crate (`ro-crate-metadata.json`) for each run, and registers any new runs as CKAN datasets in the `type-model-run` group. For a run of a model that is not registered yet, it writes the model's FDO to the lakeFS repo `models` (from the model card, see below) and creates its CKAN entry; for a registered model it brings the CKAN entry in step with the FDO in lakeFS. It never rewrites a model FDO in lakeFS; that is the models flow's job, since runs of the same model are synced concurrently.

Existing run entries are skipped unless `force_recreate=True`. **`force_recreate=True` is not concurrency-safe** — model handling is per-run and all run tasks run concurrently, so N runs of the same model race on the lakeFS `models` commit (`predicate failed`) and the CKAN model-descriptor delete/recreate (`URL already in use`, 500s). Use it only against a stopped scheduler, and only expect the run entries themselves to be rebuilt cleanly.

### `sync_ckan_with_lakefs_models` — models

**File:** `flow/sync_ckan_with_lakefs_models.py`  
**Deployment:** `sync-ckan-with-lakefs-models` (hourly at :20, `MODELS_SYNC_CRON`)

For each model FDO in the lakeFS repo `models`: fetches the model card (`fdo.json` in the model's GitHub repository `ghcr.io/<owner>/<repo>` → `github.com/<owner>/<repo>`, default branch), rewrites the FDO's profile when the card changed, and creates or updates the model's CKAN entry in the group `type-model`.

The model card follows the spec `model-card` v1 of [`episerve_specs`](https://github.com/The-EPISERVE-Consortium/episerve_specs). Its sections become CKAN extras with JSON values: `model_task`, `model_parameters` (the JSON Schema of `config.json`), `model_inputs`, `model_outputs`, `model_resources`, `model_documentation`. The card is validated with `episerve_specs`; the result is stored as `model_card_valid` (`true`/`false`) and `model_card_validation` (issues and validator version). An invalid card is still registered. A model whose repository has no card keeps a placeholder FDO and gets `model_card_valid = false`. The extras, notes and url of an existing entry are updated in place; `force_recreate=True` deletes and recreates the entry (and re-uploads its FDO resource).

### `sync_ckan_with_lakefs_benchmarks` — generated benchmark files

**File:** `flow/sync_ckan_with_lakefs_benchmarks.py`  
**Deployment:** `sync-ckan-with-lakefs-benchmarks` (hourly at :25, `BENCHMARKS_SYNC_CRON`)

The API server stores the input and the reference file of every finalized benchmark as one FDO object with two components (own QID, `.fdo.json` sidecar) in the lakeFS repository `benchmarks`. The flow creates a CKAN dataset for each new object in the group `type-benchmark-data` (so the files are not offered as source datasets), with one resource per file and the extras `benchmark_id`, `source_qids`, `data_commit` and `access = open`. Objects never change, so existing ones are skipped. The group must exist in CKAN. Deploy only this flow with `python -c "import deploy"`-style code or by running `deploy.py` (which redeploys all four).

### `sync_ckan_with_lakefs_dataprocessed` — processed datasets

**File:** `flow/sync_ckan_with_lakefs_dataprocessed.py`  
**Deployment:** `sync-ckan-with-lakefs-dataprocessed` (daily 04:00 Europe/Berlin, after the dataset downloads at 01:00; `DATAPROCESSED_SYNC_CRON`)

Scans `data-processed` in lakeFS for `.fdo.json` sidecar files, reads the FDO metadata, and registers any new datasets as CKAN datasets in the `type-raw-data` group. Supports `update=True` to overwrite metadata of already-registered entries.

## Project structure

```
flow/
  sync_ckan_with_lakefs_modelruns.py      # model runs → CKAN
  sync_ckan_with_lakefs_dataprocessed.py  # data-processed → CKAN
  sync_ckan_with_lakefs_benchmarks.py     # benchmarks → CKAN (group type-benchmark-data)
  sync_ckan_with_lakefs_models.py         # models (model cards) → lakeFS models + CKAN (group type-model)
tools/
  ckan_tools.py      # CKAN API helpers
  lakefs_tools.py    # lakeFS listing and metadata reading
  sharding.py        # QID sharding logic
tests/               # pytest unit tests
deploy.py            # creates/updates the four Prefect deployments
Dockerfile           # python:3.12-slim image for the Prefect worker
.github/workflows/   # build → push → deploy pipeline
```

## lakeFS path structure

```
model-runs/main/
  <pp>/<qq>/<rr>/<QID>/
    <QID>.fdo.json              ← FAIR Digital Object descriptor
    components/
      input/                    ← staged input files
      output/                   ← model output files
      ro-crate-metadata.json    ← RO-Crate 1.1 provenance

data-processed/main/
  <pp>/<qq>/<rr>/<QXXX>/
    <QXXX>.fdo.json             ← FDO metadata sidecar
    components/
      <source-stem>.parquet
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `CKAN_API_TOKEN` | yes | CKAN API token with write access |
| `CKAN_HOST` | yes | CKAN endpoint used for API calls (may be an in-cluster Service DNS) |
| `CKAN_PUBLIC_URL` | no | Public CKAN base for the "Browse all runs" / dataset links written into CKAN notes; defaults to `CKAN_HOST`. Set this when `CKAN_HOST` is in-cluster so catalog links stay externally resolvable (mirrors `DOIP_PUBLIC_URL`). |
| `LAKEFS_ACCESS_KEY` | yes | lakeFS access key ID |
| `LAKEFS_SECRET_KEY` | yes | lakeFS secret access key |
| `LAKEFS_HOST` | no | lakeFS endpoint (default: `https://lake-episerve.zib.de`) |
| `DOIP_HOST` | yes | DOIP server base (protocol access) |
| `DOIP_PUBLIC_URL` | no | Public DOIP base used for the retrieve links written into CKAN resources; defaults to `DOIP_HOST`. Set this when `DOIP_HOST` points at an in-cluster Service DNS so catalog links stay externally resolvable. |
| `PREFECT_API_URL` | deploy | Prefect server URL (needed by `deploy.py` and CI) |
| `PREFECT_API_KEY` | deploy | Prefect API key (needed by `deploy.py` and CI) |

## Running locally

```bash
pip install -r requirements.txt

export CKAN_API_TOKEN=...
export LAKEFS_ACCESS_KEY=...
export LAKEFS_SECRET_KEY=...

# sync model runs once
python -c "from flow.sync_ckan_with_lakefs_modelruns import sync_ckan_with_lakefs; sync_ckan_with_lakefs()"

# sync processed datasets once
python -c "from flow.sync_ckan_with_lakefs_dataprocessed import sync_ckan_with_lakefs_dataprocessed; sync_ckan_with_lakefs_dataprocessed()"
```

## Running tests

```bash
pip install -r requirements.txt pytest
pytest tests/
```

## Deploying

CI only tests and builds the image; it does not register deployments. Register them by hand:

```bash
export PREFECT_API_URL=http://prefect-server.default.svc.cluster.local:4200/api
export PREFECT_API_KEY=...
export CKAN_API_TOKEN=...
export LAKEFS_ACCESS_KEY=...
export LAKEFS_SECRET_KEY=...

python deploy.py
```

This creates or updates the deployments `sync-ckan-with-lakefs-modelruns`, `sync-ckan-with-lakefs-dataprocessed`, `sync-ckan-with-lakefs-benchmarks` and `sync-ckan-with-lakefs-models` on the `kubernetes-pool` work pool. The image `ghcr.io/the-episerve-consortium/sync-ckan-with-lakefs:latest` must already exist (CI builds and pushes it).
