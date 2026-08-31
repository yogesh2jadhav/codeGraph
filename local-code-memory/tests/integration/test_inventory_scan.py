import json

from code_memory.models.inventory import FileKind
from code_memory.pipeline import run_scan
from code_memory.scanner import InventoryScanner


def test_full_scan_produces_artifacts(spring_sample, config_for):
    cfg = config_for(spring_sample)
    ctx, result = run_scan(cfg, mode="full")

    inv = result.inventory
    kinds = inv.counts_by_kind()
    assert kinds.get("java_main") == 2
    assert kinds.get("java_test") == 1
    assert kinds.get("maven_pom") == 1
    assert kinds.get("sql") == 1
    assert kinds.get("app_config") == 1

    assert inv.build.build_system == "maven"
    assert inv.build.spring_boot_version == "3.3.2"
    assert "PostgreSQL" in inv.build.database_drivers

    inv_json = spring_sample / ".code-memory" / "project_inventory.json"
    overview = spring_sample / ".code-memory" / "context" / "00_project_overview.md"
    assert inv_json.is_file()
    assert overview.is_file()

    data = json.loads(inv_json.read_text())
    assert data["file_count"] == len(inv.files)
    assert "Spring Boot" in overview.read_text()

    # metadata persisted
    scan = ctx.store.get_scan(ctx.scan_id)
    assert scan["status"] in ("success", "partial")
    assert ctx.store.summary()["tracked_files"] == len(inv.files)


def test_incremental_detects_change(spring_sample, config_for):
    cfg = config_for(spring_sample)
    run_scan(cfg, mode="full")

    target = spring_sample / "src/main/java/com/example/UserService.java"
    target.write_text(target.read_text() + "\n// touched\n", encoding="utf-8")
    new_file = spring_sample / "src/main/java/com/example/Extra.java"
    new_file.write_text("package com.example;\nclass Extra {}\n", encoding="utf-8")

    _, result = run_scan(cfg, mode="incremental")
    assert "src/main/java/com/example/Extra.java" in result.added
    assert "src/main/java/com/example/UserService.java" in result.modified
    assert result.unchanged > 0


def test_oversized_file_is_skipped_with_warning(spring_sample, config_for):
    cfg = config_for(spring_sample)
    cfg.data["scanner"]["max_file_size_mb"] = 0  # everything is "oversized"
    scanner = InventoryScanner(cfg)
    result = scanner.scan(write_artifacts=False)
    assert result.inventory.files == []
    assert any("oversized" in w for w in result.inventory.warnings)


def test_excluded_dirs_pruned(spring_sample, config_for):
    (spring_sample / "target" / "classes").mkdir(parents=True)
    (spring_sample / "target" / "classes" / "Junk.java").write_text("x", encoding="utf-8")
    cfg = config_for(spring_sample)
    result = InventoryScanner(cfg).scan(write_artifacts=False)
    assert all("target/" not in e.relative_path for e in result.inventory.files)
