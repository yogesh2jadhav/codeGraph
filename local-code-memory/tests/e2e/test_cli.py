"""End-to-end CLI smoke tests via the argparse entry point."""

from code_memory.cli.main import main


def test_cli_scan_stats_validate(spring_sample, tmp_path, capsys, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "application.yaml").write_text(
        f"project:\n  root: {spring_sample}\n"
        f"storage:\n  metadata: {tmp_path / 'm.db'}\n"
        "logging:\n  file:\n    enabled: false\n",
        encoding="utf-8",
    )
    args_base = ["-c", str(cfg_dir / "application.yaml")]

    assert main(args_base + ["init"]) == 0
    assert main(args_base + ["scan"]) == 0
    out = capsys.readouterr().out
    assert "build system:  maven" in out

    assert main(args_base + ["stats", "--json"]) == 0
    assert '"tracked_files"' in capsys.readouterr().out

    assert main(args_base + ["validate"]) == 0
    assert "validate: ok" in capsys.readouterr().out


def test_cli_pending_command_reports_phase(capsys):
    assert main(["context"]) == 2
    assert "Phase 11" in capsys.readouterr().out
