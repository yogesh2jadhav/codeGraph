from code_memory.embeddings import HashingEmbeddingProvider
from code_memory.graph import build_graph
from code_memory.parsers.java import parse_java_source
from code_memory.vector.index import build_vector_index
from code_memory.vector.store import InMemoryVectorStore

A = b"""
package p;
public class A {
    public void one() {}
    public void two() {}
}
"""
A2 = b"""
package p;
public class A {
    public void one() { helper(); }
    public void two() {}
    private void helper() {}
}
"""


def _index(src, path, embedder):
    pf = parse_java_source("A.java", src)
    g = build_graph([pf])
    store = InMemoryVectorStore(path)
    stats = build_vector_index(g, [pf], path.parent, embedder, store)
    return stats, store


def test_incremental_reuses_unchanged_vectors(tmp_path):
    emb = HashingEmbeddingProvider(64)
    idx = tmp_path / "idx.json"

    s1, _ = _index(A, idx, emb)
    assert s1["embedded"] == s1["chunks"] and s1["reused"] == 0

    # same source -> everything reused, nothing re-embedded
    s2, _ = _index(A, idx, emb)
    assert s2["embedded"] == 0
    assert s2["reused"] == s2["chunks"] > 0


def test_incremental_embeds_only_changed_and_prunes(tmp_path):
    emb = HashingEmbeddingProvider(64)
    idx = tmp_path / "idx.json"
    _index(A, idx, emb)

    s, store = _index(A2, idx, emb)
    # A.one changed + new helper() method -> a few new/changed chunks embedded,
    # the rest reused; no stale chunks left
    assert 0 < s["embedded"] < s["chunks"]
    assert s["reused"] >= 1
    assert store.count() == s["chunks"]


def test_rebuild_reembeds_everything(tmp_path):
    emb = HashingEmbeddingProvider(64)
    idx = tmp_path / "idx.json"
    _index(A, idx, emb)

    pf = parse_java_source("A.java", A)
    g = build_graph([pf])
    store = InMemoryVectorStore(idx)
    stats = build_vector_index(g, [pf], tmp_path, emb, store, incremental=False)
    assert stats["embedded"] == stats["chunks"]
    assert stats["reused"] == 0
