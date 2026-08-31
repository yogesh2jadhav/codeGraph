from code_memory.metadata import MetadataStore


def test_scan_lifecycle(tmp_path):
    store = MetadataStore(tmp_path / "m.db")
    store.start_scan(scan_id="s1", project_root="/x", mode="full",
                     scanner_version="1", schema_version="1",
                     git_commit="abc", git_branch="main")
    store.upsert_files(
        [{"relative_path": "A.java", "kind": "java_main", "size_bytes": 10,
          "sha256": "h1", "lines": 2}],
        scan_id="s1", scanner_version="1",
    )
    store.record_event("s1", "warning", "something", phase="inventory")
    store.finish_scan("s1", "success", {"file_count": 1})

    scan = store.get_scan("s1")
    assert scan["status"] == "success"
    assert store.known_file_hashes() == {"A.java": "h1"}
    assert len(store.events("s1")) == 1
    assert store.summary()["tracked_files"] == 1


def test_incremental_hash_change(tmp_path):
    store = MetadataStore(tmp_path / "m.db")
    store.start_scan(scan_id="s1", project_root="/x", mode="full",
                     scanner_version="1", schema_version="1",
                     git_commit=None, git_branch=None)
    store.upsert_files([{"relative_path": "A.java", "kind": "java_main",
                         "size_bytes": 1, "sha256": "h1", "lines": 1}],
                       scan_id="s1", scanner_version="1")
    store.upsert_files([{"relative_path": "A.java", "kind": "java_main",
                         "size_bytes": 2, "sha256": "h2", "lines": 1}],
                       scan_id="s2", scanner_version="1")
    assert store.known_file_hashes()["A.java"] == "h2"
    store.delete_files(["A.java"])
    assert store.known_file_hashes() == {}
