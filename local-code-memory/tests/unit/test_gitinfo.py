from code_memory.scanner.gitinfo import read_git_info


def test_no_repo(tmp_path):
    assert read_git_info(tmp_path) == (None, None)


def test_branch_and_loose_ref(tmp_path):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n", encoding="utf-8")
    assert read_git_info(tmp_path) == ("a" * 40, "main")


def test_packed_ref(tmp_path):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/dev\n", encoding="utf-8")
    (git / "packed-refs").write_text(
        "# pack-refs with: peeled\n" + "b" * 40 + " refs/heads/dev\n",
        encoding="utf-8",
    )
    assert read_git_info(tmp_path) == ("b" * 40, "dev")


def test_detached_head(tmp_path):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("c" * 40 + "\n", encoding="utf-8")
    assert read_git_info(tmp_path) == ("c" * 40, None)
