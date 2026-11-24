# Test the tilegen runner, locally and in Prefect

Covers `python -m tilegen.runner` (`tilegen/runner.py`), the code that replaced the bash in `.github/workflows/tilegen.yml`. Do the steps in order: each one costs more than the one before.

Conventions:
- Commands run in **bash** on the shared JupyterHub instance, from the repo root.
- Each step lists **Why**, **Depends on** and **Replace**.
- Steps 4 to 6 write to S3 (`suyana-dev-tiles`), but only the missing days of one scene. Re-running them is safe.

What the runner does: it turns `--job` (plus optional `--scenes`, `--variables`, `--days`) into one `tilegen run ...` call. With no options it runs the same scenes, variables and look-back windows the old workflow ran. Scene lists come from `tilegen/conf/scenes.yaml`: land jobs skip the `indice` scenes (`nino34`), `era5` and `era5-swvl1` run all of them.

| Setting | Where it comes from | Default |
|---|---|---|
| Job (product) | `--job` | required: `cpc chirps era5 era5-swvl1 chirps-v3 era5-land chirts-era5 cobertura` (`chirts-era5` is experimental and not scheduled) |
| Scenes | `--scenes a b c` | the job's list from `scenes.yaml` |
| Variables | `--variables v1 v2` | the job's list (`era5`: 8 variables; others: all of the dataset) |
| Dates | `--start-date YYYY-MM-DD` `--end-date YYYY-MM-DD` (replace `--days`; the end is still capped at today minus the dataset's `lag_days`) | the look-back window below |
| Look-back days | `--days N` | the job's window (cpc 45, chirps 70, era5 16, era5-swvl1 90, chirps-v3 70, era5-land 120) |
| Bucket | env `TILEGEN_BUCKET` | `s3.bucket` in `tilegen/conf/config.yaml` (`suyana-dev-tiles`) |
| Scratch dir | env `TILEGEN_WORKDIR` | a temp dir, deleted at the end |
| CDS and EDH keys | env `CDSAPIRC`, `NETRC` (file contents) | the real `~/.cdsapirc` and `~/.netrc` |

The scratch dir holds the downloaded files (NetCDF, GeoTIFF) while they are cropped and uploaded. It is needed, but it does not have to survive the run. Set `TILEGEN_WORKDIR` only to keep downloads between runs.

---

## Step 1. Session variables

**Why:** later commands read these values, so you set them once. **Depends on:** nothing.

```bash
REPO="$HOME/projects/daily-climate-tilegen"
BRANCH="exp/update-chirps"
SCENE="peru"
JOB_NAME="$USER-tilegen-test"
```

**Replace:** `REPO` with your clone; `BRANCH` with the branch you pushed; `SCENE` with any scene from `tilegen scenes`. `JOB_NAME` is custom (start it with your username so names do not clash).
**Captures:** `REPO`, `BRANCH`, `SCENE`, `JOB_NAME` (used in all later steps). Set them again in a new terminal.

Check: `cd "$REPO" && git branch --show-current` must print the value of `$BRANCH`.

## Step 2. Build the Python 3.12 environment

**Why:** the lock needs Python 3.12 (zarr 3), and the system Python is 3.10. This is the same build Prefect does. **Depends on:** Step 1 (`$REPO`).

```bash
cd "$REPO"
~/prefect/venv/bin/uv venv --python 3.12 .venv312
~/prefect/venv/bin/uv pip install --python .venv312/bin/python -r requirements.txt pytest
~/prefect/venv/bin/uv pip install --python .venv312/bin/python -e .
```

Check: `.venv312/bin/python -c "import tilegen, zarr; print(zarr.__version__)"` must print `3.2.1`.
If `uv` is not found, ask the admin: it lives in the platform's venv. `.venv312` is untracked; delete it when done.

## Step 3. Unit tests and dry run (no network)

**Why:** proves the job table, scene lists, date window and credential handling without touching anything. **Depends on:** Step 2.

```bash
.venv312/bin/python -m pytest -q tests/test_runner.py
.venv312/bin/python -m tilegen.runner --job era5 --scenes "$SCENE" --variables t2m_mean --days 10 --dry-run
.venv312/bin/python -m tilegen.runner --job cobertura --dry-run
```

Check: pytest ends with `12 passed`. The first dry run prints one line like
`tilegen run -d era5 -v t2m_mean -x peru -s 2026-11-13` (start = today UTC minus 10 days). The second prints `python deploy/publicar_docs.py`.
Check the defaults too: `--job era5 --dry-run` must list 8 `-v` and 21 `-x` (the 20 land scenes plus `nino34`); `--job cpc --dry-run` must list 20 `-x` and no `nino34`.
An unknown scene exits with `unknown scene(s) [...]` and lists the valid names.

## Step 4. Real run on one scene, no secrets

**Why:** proves download, crop and upload work through the runner, with the instance's AWS role. **Depends on:** Step 3. CPC and CHIRPS need no key.

First a run that writes nothing to S3. `--local-only` creates the cube under the run's temp dir and deletes it at the end:

```bash
.venv312/bin/python -m tilegen.runner --job chirps-v3 --scenes "$SCENE" --days 47 --local-only
echo "exit code: $?"
```

Check: one `downloaded ...` line per file, `done ...` per day, exit code `0`. `--days 47` is a 3-day window for `chirps-v3` (its data ends 45 days before today). If you get `empty date range after clamping`, the window ends before the source has data: raise `--days`.

Then check the sharing. With several scenes the global daily files must be downloaded once, not once per scene:

```bash
.venv312/bin/python -m tilegen.runner --job chirps-v3 --scenes argentina_norte uruguay paraguay --days 47 --local-only 2>&1 | grep -c "downloaded"
```

Check: it prints `6` (3 days x 2 variables), not `18`, and the output has a `ventana compartida: 6 pedido(s) al origen para 3 escena(s)` line. With one scene there is no such line: the file is downloaded and cropped straight away.

Now the real run, which writes the missing days to `$TILEGEN_BUCKET`:

```bash
.venv312/bin/python -m tilegen.runner --job cpc --scenes "$SCENE" --days 10
echo "exit code: $?"
.venv312/bin/tilegen verify -d cpc -x "$SCENE"
```

Check: the runner prints the plan, then `nothing to do` or a JSON summary with `"errors": 0`; the exit code is `0`; `verify` shows the last day close to today (CPC lags a few days). If you get an S3 `AccessDenied`, the instance role cannot write to `$TILEGEN_BUCKET` (default `suyana-dev-tiles`). A `429` from the source means it is rate limiting you: the run waits what the server asks (`Retry-After`) and fails that day if it still gets 429; the next run retries it.

## Step 5. Real run with keys passed as environment variables

**Why:** Prefect gives secrets as environment variables, not as `~/.cdsapirc` and `~/.netrc`. This step proves the runner writes them itself. **Depends on:** Step 4.

```bash
HOME_EMPTY="$(mktemp -d)"
CDSAPIRC="$(cat ~/.cdsapirc)" NETRC="$(cat ~/.netrc)" HOME="$HOME_EMPTY" \
  .venv312/bin/python -m tilegen.runner --job era5 --scenes "$SCENE" --variables t2m_mean --days 20
echo "exit code: $?"
rm -r "$HOME_EMPTY"
```

**Replace:** nothing; your own key files are read only to fill the two variables.
Check: exit code `0` and no `Missing/incomplete configuration file` or `401` error. Because `HOME` is empty, the run cannot fall back to your real key files.
If it fails with an AWS credentials error, your `~/.aws` was needed for S3 and the empty `HOME` hid it. The instance role normally covers this; if it does not, test with `~/.cdsapirc` and `~/.netrc` temporarily renamed instead of changing `HOME`, and rename them back after.
The EDH path (`era5-swvl1`, `~/.netrc`) is a separate check: `... --job era5-swvl1 --scenes "$SCENE" --days 20` with the same variables.

## Step 6. Register one job in Prefect

**Why:** proves the platform can clone the branch, build the venv from `requirements.txt` and run the module. **Depends on:** Step 5; the branch must be pushed, including `requirements.txt` and `tilegen/runner.py`.

```bash
git push origin "$BRANCH"
prefect-job add \
  --name "$JOB_NAME" \
  --repo insuyana/daily-climate-tilegen \
  --branch "$BRANCH" \
  --file tilegen.runner \
  --python 3.12 \
  -- --job cpc --scenes "$SCENE" --days 10
```

**Replace:** `cpc` with the job to try. `--file tilegen.runner` runs `python -m tilegen.runner`, so the platform does `pip install -r requirements.txt` and then `pip install -e .` first.
For `era5`, `era5-land` and `era5-swvl1`, add the two secrets on the register form (https://jupyter.suyana.io/services/prefect/register): type the job name `$JOB_NAME` and fill **Secrets** with one line, writing each newline of the file as a literal `\n`:

```
CDSAPIRC='url: <cds url>\nkey: <cds key>' NETRC='machine <host> login <user> password <key>'
```

The runner turns `\n` into a real newline and writes the files for that run only. Values are never shown again. Do not use `--env` for them. I did not test that the form keeps this quoting; if a run fails with a CDS configuration error, check this first.
No `TILEGEN_BUCKET`, `TILEGEN_WORKDIR` or `AWS_DEFAULT_REGION` is needed: the runner sets the defaults.

Check: `prefect-job list` shows `$JOB_NAME`.

## Step 7. Run it and read the logs

**Why:** the first real run on the platform: clone, venv, tilegen, exit code. **Depends on:** Step 6.

```bash
prefect-job run --name "$JOB_NAME"
```

Open https://jupyter.suyana.io/services/prefect/ > **Runs**, click the run.
Check: the state ends **Completed**; the log shows the clone, `pip install`, then the same plan lines as Step 4. Secret values show as `***`. A first run is slower (it builds the venv).
To change the arguments for one run: **Deployments** > your job > **Run** > **Custom run**. Put **one argument per item**: `--job`, `era5`, `--scenes`, `peru`, `--days`, `10` is six items.
Common failures:
- `entrypoint ... not found`: wrong `--file` or the branch is not pushed.
- `ModuleNotFoundError`: `requirements.txt` missing on that branch.
- `unknown scene(s)`: the scene is not in `scenes.yaml` on that branch.

## Step 8. Register the real jobs, then clean up

**Why:** the production schedule: one deployment per job, same times as the workflow (Prefect cron has one schedule per deployment). **Depends on:** Step 7 passing for at least one CDS job and one non-CDS job.

| Job (`--name` suffix) | Cron (UTC) | Trailing args |
|---|---|---|
| `cpc` | `0 5 * * *` | `-- --job cpc` |
| `chirps` | `0 6 * * *` | `-- --job chirps` |
| `era5` | `30 6 * * *` | `-- --job era5` |
| `era5-swvl1` | `0 9 * * 0` | `-- --job era5-swvl1` |
| `chirps-v3` | `30 9 * * *` | `-- --job chirps-v3` |
| `era5-land` | `30 10 * * *` | `-- --job era5-land` |
| `cobertura` | `0 12 * * *` | `-- --job cobertura` |

Use `--branch` of the tag or branch you want in production, `--timezone UTC`, `--python 3.12`, and the same secrets on each job that needs them (`era5`, `era5-swvl1`, `era5-land`).
Do not switch these on while the GitHub schedule (`TILEGEN_ACTIVO=true`) or the old crontab still writes the same cubes: two writers on one store are not safe. The platform limits one run per job, not across jobs.

Clean up the test job and environment:

```bash
prefect-job delete --name "$JOB_NAME"
rm -r "$REPO/.venv312"
```

Notifications (ClickUp) are not part of the runner: a failed Prefect run shows **Failed** in the UI and nothing more.
