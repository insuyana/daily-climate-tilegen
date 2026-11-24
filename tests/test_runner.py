import os
import stat
from datetime import date
from pathlib import Path

import pytest

from tilegen import runner

TODAY = date(2026, 11, 23)
LAND = ("mexico_norte mexico_sur centroamerica antillas_mayores antillas_menores colombia venezuela guayanas "
        "ecuador_galapagos peru bolivia brasil_amazonia brasil_nordeste brasil_centro_sudeste brasil_sur "
        "paraguay uruguay chile_norte_centro argentina_norte patagonia").split()


def xs(args):
    return [args[i + 1] for i, a in enumerate(args) if a == "-x"]


def test_land_job_skips_nino34_and_matches_the_old_workflow_list():
    args = runner.build_args("cpc", today=TODAY)
    assert xs(args) == LAND
    assert args[:3] == ["run", "-d", "cpc"] and args[-2:] == ["-s", "2026-10-09"]  # 45 days


def test_era5_runs_every_scene_with_the_eight_variables():
    args = runner.build_args("era5", today=TODAY)
    assert xs(args) == LAND + ["nino34"]
    assert [args[i + 1] for i, a in enumerate(args) if a == "-v"] == list(runner.ERA5_VARS)
    assert args[-2:] == ["-s", "2026-11-07"]  # 16 days


def test_swvl1_uses_the_edh_source():
    args = runner.build_args("era5-swvl1", today=TODAY)
    assert args[:7] == ["run", "-d", "era5", "--source", "era5_edh", "-v", "swvl1"]
    assert args[-2:] == ["-s", "2026-08-25"]  # 90 days


def test_overrides_replace_job_defaults():
    args = runner.build_args("era5", ["peru"], ["t2m_max"], 3, today=TODAY)
    assert args == ["run", "-d", "era5", "-v", "t2m_max", "-x", "peru", "-s", "2026-11-20"]


def test_unknown_scene_is_rejected():
    with pytest.raises(ValueError, match="atlantis"):
        runner.build_args("cpc", ["atlantis"])


def test_runtime_env_writes_private_credential_files_and_cleans_up(monkeypatch):
    for k in ("TILEGEN_WORKDIR", "CDSAPI_RC"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CDSAPIRC", "url: u\nkey: k")
    monkeypatch.setenv("NETRC", "machine m login l password p\n")
    with runner.runtime_env():
        rc, netrc = Path(os.environ["CDSAPI_RC"]), Path(os.environ["NETRC"])
        assert rc.read_text() == "url: u\nkey: k\n"
        assert netrc.read_text() == "machine m login l password p\n"
        assert stat.S_IMODE(rc.stat().st_mode) == 0o600
        assert Path(os.environ["TILEGEN_WORKDIR"]).is_dir()
    assert not rc.exists()
    assert "CDSAPI_RC" not in os.environ and "TILEGEN_WORKDIR" not in os.environ
    assert os.environ["NETRC"] == "machine m login l password p\n"  # the secret's own value is back


def test_escaped_newlines_become_real_ones(monkeypatch):
    monkeypatch.setenv("CDSAPIRC", "url: u\\nkey: k")
    monkeypatch.delenv("CDSAPI_RC", raising=False)
    with runner.runtime_env():
        assert Path(os.environ["CDSAPI_RC"]).read_text() == "url: u\nkey: k\n"


def test_runtime_env_keeps_user_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("TILEGEN_WORKDIR", str(tmp_path))
    monkeypatch.setenv("TILEGEN_BUCKET", "other")
    monkeypatch.delenv("CDSAPIRC", raising=False)
    with runner.runtime_env():
        assert os.environ["TILEGEN_WORKDIR"] == str(tmp_path) and os.environ["TILEGEN_BUCKET"] == "other"
    assert os.environ["TILEGEN_WORKDIR"] == str(tmp_path)


def test_dry_run_prints_the_command(capsys):
    assert runner.main(["--job", "chirps", "--scenes", "peru", "--days", "2", "--dry-run"]) == 0
    assert capsys.readouterr().out.startswith("tilegen run -d chirps -x peru -s ")


def test_cobertura_dry_run(capsys):
    assert runner.main(["--job", "cobertura", "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == "python deploy/publicar_docs.py"


def test_run_passes_args_to_the_cli(monkeypatch):
    seen = {}
    monkeypatch.setattr(runner.cli, "main", lambda args, standalone_mode: seen.update(args=args))
    assert runner.main(["--job", "cpc", "--scenes", "peru", "--days", "1"]) == 0
    assert seen["args"][:5] == ["run", "-d", "cpc", "-x", "peru"]


def test_failed_scene_propagates_exit_1(monkeypatch):
    def boom(args, standalone_mode):
        raise SystemExit(1)  # what `tilegen run` does when a scene had errors
    monkeypatch.setattr(runner.cli, "main", boom)
    with pytest.raises(SystemExit) as e:
        runner.main(["--job", "cpc", "--scenes", "peru"])
    assert e.value.code == 1


def test_local_only_is_passed_through():
    args = runner.build_args("cpc", ["peru"], days=3, today=TODAY, local_only=True)
    assert args[-1] == "--local-only"
    assert "--local-only" not in runner.build_args("cpc", ["peru"], days=3, today=TODAY)


def test_explicit_dates_replace_days():
    args = runner.build_args("chirts-era5", ["peru"], today=TODAY, start_date=date(2020, 1, 1), end_date=date(2020, 1, 10))
    assert args == ["run", "-d", "chirts-era5", "-x", "peru", "-s", "2020-01-01", "-e", "2020-01-10"]


def test_start_date_alone_has_no_end():
    args = runner.build_args("cpc", ["peru"], today=TODAY, start_date=date(2026, 1, 1))
    assert args[-2:] == ["-s", "2026-01-01"] and "-e" not in args


def test_end_before_start_is_rejected():
    with pytest.raises(ValueError, match="before"):
        runner.build_args("cpc", ["peru"], start_date=date(2020, 2, 1), end_date=date(2020, 1, 1))


def test_days_and_start_date_are_exclusive():
    with pytest.raises(SystemExit):
        runner.main(["--job", "cpc", "--days", "3", "--start-date", "2020-01-01", "--dry-run"])


def test_dates_on_the_command_line(capsys):
    assert runner.main(["--job", "chirts-era5", "--scenes", "peru", "--start-date", "2020-01-01",
                        "--end-date", "2020-01-10", "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == "tilegen run -d chirts-era5 -x peru -s 2020-01-01 -e 2020-01-10"


def test_scene_groups_can_be_named():
    assert xs(runner.build_args("era5", ["land"], today=TODAY)) == LAND
    assert xs(runner.build_args("cpc", ["all"], today=TODAY)) == LAND + ["nino34"]
