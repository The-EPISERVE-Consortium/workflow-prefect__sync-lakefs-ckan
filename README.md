# sync-ckan-with-lakefs

A Prefect flow that periodically scans the lakeFS `model-runs` repository and registers any new model runs in the CKAN data catalog. Each run is created as a CKAN dataset with its input and output files linked as `lakefs://` URIs.

## What it does

1. Lists all top-level run folders in `lakefs://model-runs/main/`
2. For each run, checks whether a CKAN dataset already exists (by `extras_run_id`)
3. If not, reads `metadata.json`, collects input/output file URIs, and creates a CKAN dataset with all files attached as resources
4. Runs on an hourly cron schedule via a Prefect deployment on the `kubernetes-pool` work pool

## Project structure

```
flows/sync_ckan_with_lakefs.py   # Prefect flow and tasks
tests/                           # pytest unit tests
deploy.py                        # creates/updates the Prefect deployment
Dockerfile                       # image for the Prefect worker
.github/workflows/ci.yaml        # build → push → deploy pipeline
```

## Environment variables

| Variable           | Required | Description                                              |
|--------------------|----------|----------------------------------------------------------|
| `CKAN_API_TOKEN`   | yes      | CKAN API token with write access                         |
| `LAKEFS_ACCESS_KEY`| yes      | lakeFS access key ID                                     |
| `LAKEFS_SECRET_KEY`| yes      | lakeFS secret access key                                 |
| `LAKEFS_HOST`      | no       | lakeFS endpoint (default: `https://lake-episerve.zib.de/`) |
| `PREFECT_API_URL`  | deploy   | Prefect server URL (needed by `deploy.py` and CI)        |
| `PREFECT_API_KEY`  | deploy   | Prefect API key (needed by `deploy.py` and CI)           |

## Running locally

```bash
pip install -r requirements.txt

export CKAN_API_TOKEN=...
export LAKEFS_ACCESS_KEY=...
export LAKEFS_SECRET_KEY=...

# run the flow once
python -c "from flows.sync_ckan_with_lakefs import sync_ckan_with_lakefs; sync_ckan_with_lakefs()"
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

This creates or updates the `sync-ckan-with-lakefs` deployment on the `kubernetes-pool` work pool with an hourly schedule. The image `ghcr.io/the-episerve-consortium/sync-ckan-with-lakefs:latest` must already exist (CI builds and pushes it).

## lakeFS run folder structure

```
model-runs/main/
  <run_id>/
    metadata.json      ← provenance: model_name, git_commit, docker_tag,
                          run_timestamp, status, domain, modality
    input/             ← one or more input files
    output/            ← one or more output files
```
