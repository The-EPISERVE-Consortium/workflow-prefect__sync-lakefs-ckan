# workflow-prefect__sync-lakefs-ckan

Two Prefect flows that keep the CKAN data catalog in sync with lakeFS. One flow handles processed datasets (`data-processed`), the other handles model run results (`model-runs`). Both are intended to run on a schedule.

## Flows

### `sync_ckan_with_lakefs` — model runs

**File:** `flow/sync_ckan_with_lakefs_modelruns.py`  
**Deployment:** `sync-ckan-with-lakefs-modelruns`

Scans `model-runs` in lakeFS for QID folders, reads the FDO metadata (`<QID>.fdo.json`) and RO-Crate (`ro-crate-metadata.json`) for each run, and registers any new runs as CKAN datasets in the `type-model-run` group. Existing entries are skipped unless `force_recreate=True`.

### `sync_ckan_with_lakefs_dataprocessed` — processed datasets

**File:** `flow/sync_ckan_with_lakefs_dataprocessed.py`  
**Deployment:** `sync-ckan-with-lakefs-dataprocessed`

Scans `data-processed` in lakeFS for `.fdo.json` sidecar files, reads the FDO metadata, and registers any new datasets as CKAN datasets in the `type-raw-data` group. Supports `update=True` to overwrite metadata of already-registered entries.

## Project structure

```
flow/
  sync_ckan_with_lakefs_modelruns.py      # model runs → CKAN
  sync_ckan_with_lakefs_dataprocessed.py  # data-processed → CKAN
tools/
  ckan_tools.py      # CKAN API helpers
  lakefs_tools.py    # lakeFS listing and metadata reading
  sharding.py        # QID sharding logic
tests/               # pytest unit tests
deploy.py            # creates/updates both Prefect deployments
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
| `CKAN_URL` | no | CKAN endpoint (default: `https://data.episerve.zib.de`) |
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

CI deploys automatically on every push to `main`. To deploy manually:

```bash
export PREFECT_API_URL=http://prefect-server.default.svc.cluster.local:4200/api
export PREFECT_API_KEY=...
export CKAN_API_TOKEN=...
export LAKEFS_ACCESS_KEY=...
export LAKEFS_SECRET_KEY=...

python deploy.py
```

This creates or updates both deployments (`sync-ckan-with-lakefs-modelruns` and `sync-ckan-with-lakefs-dataprocessed`) on the `kubernetes-pool` work pool. The image `ghcr.io/the-episerve-consortium/sync-ckan-with-lakefs:latest` must already exist (CI builds and pushes it).
