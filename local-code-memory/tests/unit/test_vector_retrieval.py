import math

from code_memory.config import load_config
from code_memory.embeddings import HashingEmbeddingProvider, get_embedding_provider
from code_memory.graph import build_graph
from code_memory.graph.memory_repository import InMemoryGraphRepository
from code_memory.parsers.java import parse_java_source
from code_memory.reranking import LexicalOverlapReranker, get_reranker
from code_memory.retrieval.hybrid import HybridRetriever
from code_memory.vector.chunker import Chunk, build_chunks
from code_memory.vector.store import InMemoryVectorStore

SRC = b"""
package shop;
public class PaymentService {
    public void refundPayment(String orderId) { charge(orderId); }
    private void charge(String orderId) {}
    public void listInvoices() {}
}
"""


# -- embeddings -----------------------------------------------------
def test_hashing_embedding_deterministic_and_normalised():
    p = HashingEmbeddingProvider(dim=128)
    a = p.embed_one("refund a payment")
    b = p.embed_one("refund a payment")
    assert a == b
    assert abs(math.sqrt(sum(x * x for x in a)) - 1.0) < 1e-6
    assert len(a) == 128


def test_hashing_similarity_orders_sensibly():
    p = HashingEmbeddingProvider(dim=256)
    q = p.embed_one("refund payment order")
    near = p.embed_one("refundPayment for an order")
    far = p.embed_one("parse xml configuration file")

    def cos(u, v):
        return sum(x * y for x, y in zip(u, v))
    assert cos(q, near) > cos(q, far)


def test_embedding_factory_fallback():
    cfg = load_config()
    cfg.data["embedding"]["provider"] = "sentence-transformers"  # not installed
    assert isinstance(get_embedding_provider(cfg), HashingEmbeddingProvider)


# -- chunker + store ---------------------------------------------
def test_chunks_and_store_roundtrip(tmp_path):
    pf = parse_java_source("PaymentService.java", SRC)
    (tmp_path / "PaymentService.java").write_bytes(SRC)
    graph = build_graph([pf])
    chunks = build_chunks(graph, [pf], tmp_path)
    kinds = {c.kind for c in chunks}
    assert "Class" in kinds and "Method" in kinds
    assert any("refundPayment" in c.text for c in chunks)

    emb = HashingEmbeddingProvider(128)
    store = InMemoryVectorStore(tmp_path / "idx.json", emb.name)
    store.upsert(chunks, emb.embed([c.text for c in chunks]))
    store.save()

    reloaded = InMemoryVectorStore(tmp_path / "idx.json")
    assert reloaded.count() == len(chunks)
    hits = reloaded.search(emb.embed_one("refund a payment"), top_k=3)
    assert hits[0].chunk.node_id.startswith("method:") or hits[0].score > 0


# -- hybrid retrieval ---------------------------------------------
def test_hybrid_retriever_ranks_target_first(tmp_path):
    pf = parse_java_source("PaymentService.java", SRC)
    (tmp_path / "PaymentService.java").write_bytes(SRC)
    graph = build_graph([pf])
    chunks = build_chunks(graph, [pf], tmp_path)

    emb = HashingEmbeddingProvider(256)
    store = InMemoryVectorStore()
    store.upsert(chunks, emb.embed([c.text for c in chunks]))

    retr = HybridRetriever(graph=InMemoryGraphRepository(graph=graph),
                           store=store, embedder=emb,
                           reranker=LexicalOverlapReranker(), chunks=chunks)
    items = retr.retrieve("where is refundPayment implemented", top_k=5)
    assert items
    assert items[0].node_id == "method:shop.PaymentService#refundPayment(String)"
    # charge() is pulled in by graph expansion (callee of refundPayment)
    assert any("graph-callee" in it.sources for it in items)


def test_reranker_factory():
    cfg = load_config()
    cfg.data["retrieval"]["reranker"] = "none"
    from code_memory.reranking import NoopReranker
    assert isinstance(get_reranker(cfg), NoopReranker)
