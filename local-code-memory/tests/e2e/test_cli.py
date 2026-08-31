"""End-to-end CLI smoke tests via the argparse entry point."""

from code_memory.cli.main import main


def test_cli_scan_stats_validate(spring_sample, tmp_path, capsys, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "application.yaml").write_text(
        f"project:\n  root: {spring_sample}\n"
        f"storage:\n  metadata: {tmp_path / 'm.db'}\n"
        "logging:\n  file:\n    enabled: false\n"
        "llm:\n  provider: echo\n",   # keep the advisor offline + deterministic
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

    # Phase 7-9 commands over the freshly scanned repo
    assert main(args_base + ["graph"]) == 0
    assert '"backend": "memory"' in capsys.readouterr().out

    assert main(args_base + ["search", "create a user", "-k", "3"]) == 0
    assert "UserService" in capsys.readouterr().out

    assert main(args_base + ["impact", "createUser"]) == 0
    out = capsys.readouterr().out
    assert "direct callers" in out

    # Phase 10/11: full pack regen + a task pack
    assert main(args_base + ["context"]) == 0
    assert "context pack" in capsys.readouterr().out

    assert main(args_base + ["context", "add logging when user creation fails",
                             "--ask"]) == 0
    out = capsys.readouterr().out
    assert "task pack:" in out and "advice [implement_feature] (echo:" in out


def test_cli_unknown_command_errors(capsys):
    import pytest
    with pytest.raises(SystemExit):
        main(["frobnicate"])
