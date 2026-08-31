import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def spring_sample(tmp_path: Path) -> Path:
    """A disposable copy of the Spring/SQL sample project."""
    dst = tmp_path / "spring_sql_sample"
    shutil.copytree(FIXTURES / "spring_sql_sample", dst)
    return dst


@pytest.fixture
def config_for(tmp_path: Path):
    """Factory: build a Config pointed at a given project root."""
    from code_memory.config import load_config

    def _make(project_root: Path):
        cfg = load_config()
        cfg.data["project"]["root"] = str(project_root)
        cfg.data["storage"]["metadata"] = str(tmp_path / "metadata.db")
        cfg.data["logging"]["file"]["enabled"] = False
        return cfg

    return _make
