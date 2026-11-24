"""Run one of the daily tilegen jobs (what .github/workflows/tilegen.yml used to do in bash).

    python -m tilegen.runner --job era5-land
    python -m tilegen.runner --job era5 --scenes peru bolivia --variables t2m_mean precip_sum --days 5
    python -m tilegen.runner --job cpc --dry-run     # print the tilegen command, run nothing
    python -m tilegen.runner --job chirps-v3 --scenes peru --days 50 --local-only   # test, nothing goes to S3
    python -m tilegen.runner --job chirts-era5 --scenes peru --start-date 2020-01-01 --end-date 2020-01-10 --local-only

Scene lists come from scenes.yaml: "land" jobs run every scene except the `indice`
ones (nino34); "all" jobs run every scene. `--scenes` replaces either list.

Environment (all optional):
    CDSAPIRC, NETRC   full contents of ~/.cdsapirc and ~/.netrc. They are written to a private
                      temp dir for this run only (a scheduler that has no files, e.g. Prefect).
                      A literal \\n in the value becomes a newline, so a one-line form field works.
                      Unset: the real ~/.cdsapirc and ~/.netrc are used.
    TILEGEN_WORKDIR   scratch dir for downloads. Unset: a fresh temp dir, deleted at the end.
                      Set it to keep downloads between runs (CPC then skips past years: 304).
    TILEGEN_BUCKET    output bucket (default: s3.bucket of config.yaml, suyana-dev-tiles).
"""
from __future__ import annotations

import argparse
import contextlib
import os
import shlex
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import click

from .cli import CONFIG_DIR, cli
from .config import load_scenes

ERA5_VARS = ("t2m_mean", "t2m_max", "t2m_min", "precip_sum", "ssrd_sum", "sd_mean", "rsn_mean", "fg10_max")

# job -> what to run. `scenes`: "land" = all but `indice`, "all" = every scene. `days`: look-back window.
# era5 is ONE job on purpose: with cds_fetch_area all scenes share one download.
JOBS = {
    "cpc":        dict(dataset="cpc", scenes="land", days=45),
    "chirps":     dict(dataset="chirps", scenes="land", days=70),
    "era5":       dict(dataset="era5", scenes="all", days=16, variables=ERA5_VARS),
    "era5-swvl1": dict(dataset="era5", scenes="all", days=90, variables=("swvl1",), source="era5_edh"),
    "chirps-v3":  dict(dataset="chirps-v3", scenes="land", days=120),
    "era5-land":  dict(dataset="era5-land", scenes="land", days=120),
    "chirts-era5": dict(dataset="chirts-era5", scenes="land", days=70),  # experimental, not scheduled
    "cobertura":  dict(script="deploy/publicar_docs.py"),  # coverage + catalog, runs from the repo root
}


def build_args(job, scenes=None, variables=None, days=None, today=None, local_only=False,
               start_date=None, end_date=None) -> list[str]:
    """The `tilegen` argument list for a job (pure: no I/O besides reading scenes.yaml)."""
    spec = JOBS[job]
    known = load_scenes(CONFIG_DIR)
    if scenes in (["land"], ["all"]):  # a group instead of names: all but `indice` scenes, or every scene
        scenes = [n for n, s in known.items() if scenes == ["all"] or s.type != "indice"]
    if scenes:
        unknown = [s for s in scenes if s not in known]
        if unknown:
            raise ValueError(f"unknown scene(s) {unknown}; choose from {list(known)}")
    else:
        scenes = [n for n, s in known.items() if spec["scenes"] == "all" or s.type != "indice"]
    today = today or datetime.now(timezone.utc).date()
    start = start_date or today - timedelta(days=days if days is not None else spec["days"])
    if end_date and end_date < start:
        raise ValueError(f"end date {end_date} is before start date {start}")
    args = ["run", "-d", spec["dataset"]]
    if spec.get("source"):
        args += ["--source", spec["source"]]
    for v in variables or spec.get("variables", ()):
        args += ["-v", v]
    for s in scenes:
        args += ["-x", s]
    args += ["-s", start.isoformat()]
    if end_date:  # tilegen still caps it at today minus the dataset's lag_days
        args += ["-e", end_date.isoformat()]
    return args + (["--local-only"] if local_only else [])


@contextlib.contextmanager
def runtime_env():
    """Defaults and credential files for this process; everything is undone on exit."""
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    before = {k: os.environ.get(k) for k in ("TILEGEN_WORKDIR", "CDSAPI_RC", "NETRC")}  # NETRC is both the secret and the path var
    with tempfile.TemporaryDirectory(prefix="tilegen-") as tmp:  # mode 0700
        if not os.environ.get("TILEGEN_WORKDIR"):
            work = Path(tmp, "work")
            work.mkdir()
            os.environ["TILEGEN_WORKDIR"] = str(work)
        # secret var -> (file name, env var the library reads to find the file)
        for var, (name, points_to) in {"CDSAPIRC": (".cdsapirc", "CDSAPI_RC"), "NETRC": (".netrc", "NETRC")}.items():
            if os.environ.get(var):
                path = Path(tmp, name)
                path.write_text(os.environ[var].replace("\\n", "\n").rstrip("\n") + "\n")
                path.chmod(0o600)
                os.environ[points_to] = str(path)
        try:
            yield
        finally:
            for k, v in before.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job", required=True, choices=list(JOBS))
    ap.add_argument("--scenes", nargs="+", help="scene names, or `land` (all but nino34) / `all` (default: the job's list)")
    ap.add_argument("--variables", nargs="+", help="only these variables (default: the job's list; none = all of the dataset)")
    when = ap.add_mutually_exclusive_group()
    when.add_argument("--days", type=int, help="look back this many days from today, UTC (default: the job's window)")
    when.add_argument("--start-date", type=date.fromisoformat, metavar="YYYY-MM-DD", help="first day, instead of --days")
    ap.add_argument("--end-date", type=date.fromisoformat, metavar="YYYY-MM-DD", help="last day (default: the latest the source has, today minus lag_days)")
    ap.add_argument("--local-only", action="store_true", help="write under the scratch dir instead of S3 (to test a job without touching the bucket)")
    ap.add_argument("--dry-run", action="store_true", help="print what would run, run nothing")
    a = ap.parse_args(argv)

    if "script" in JOBS[a.job]:
        script = Path(JOBS[a.job]["script"])
        if a.dry_run:
            print(f"python {script}")
            return 0
        if not script.is_file():
            raise SystemExit(f"{script} not found: run from the repo root")
        with runtime_env():
            return subprocess.run([sys.executable, str(script)]).returncode

    try:
        args = build_args(a.job, a.scenes, a.variables, a.days, local_only=a.local_only,
                          start_date=a.start_date, end_date=a.end_date)
    except ValueError as exc:
        raise SystemExit(str(exc))
    if a.dry_run:
        print("tilegen " + shlex.join(args))
        return 0
    with runtime_env():
        try:
            cli.main(args=args, standalone_mode=False)  # tilegen exits 1 if any scene had errors
        except click.ClickException as exc:
            exc.show()
            return exc.exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main())
