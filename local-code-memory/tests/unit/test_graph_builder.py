from code_memory.graph import build_graph
from code_memory.models.graph import Confidence
from code_memory.parsers.java import parse_java_source

SERVICE = b"""
package com.example.svc;
import com.example.repo.UserRepository;
public class UserService extends BaseService implements Runnable {
    @Autowired private UserRepository repo;
    public void run() {}
    public User create(String name) { return null; }
}
"""
BASE = b"package com.example.svc; public class BaseService {}"
REPO = b"package com.example.repo; public interface UserRepository {}"


def build(*sources):
    parsed = [parse_java_source(f"F{i}.java", s) for i, s in enumerate(sources)]
    return build_graph(parsed)


def node(graph, node_id):
    return next(n for n in graph.nodes if n.id == node_id)


def edges_of(graph, etype):
    return [e for e in graph.edges if e.type == etype]


def test_declares_edges_and_locations():
    g = build(SERVICE, BASE, REPO)
    t = node(g, "type:com.example.svc.UserService")
    assert t.kind == "Class"
    assert t.properties["location"]["line_start"] == 4

    declares = {(e.src, e.dst) for e in edges_of(g, "DECLARES")}
    assert ("file:F0.java", "type:com.example.svc.UserService") in declares
    assert ("type:com.example.svc.UserService",
            "method:com.example.svc.UserService#create(String)") in declares
    assert ("type:com.example.svc.UserService",
            "field:com.example.svc.UserService#repo") in declares


def test_extends_implements_resolution_confidence():
    g = build(SERVICE, BASE, REPO)
    ext = edges_of(g, "EXTENDS")[0]
    assert ext.dst == "type:com.example.svc.BaseService"
    assert ext.confidence == Confidence.HIGH          # same package, declared

    impl = edges_of(g, "IMPLEMENTS")[0]
    assert impl.dst == "type:java.lang.Runnable"
    assert impl.confidence == Confidence.MEDIUM       # java.lang fallback


def test_unresolved_placeholder_for_external_annotation():
    g = build(SERVICE, BASE, REPO)
    unresolved_ids = {n.id for n in g.unresolved()}
    assert "type:Autowired" in unresolved_ids
    anno = edges_of(g, "ANNOTATED_WITH")[0]
    assert anno.confidence == Confidence.UNKNOWN


def test_placeholder_upgraded_when_declaration_seen_later():
    # SERVICE imports com.example.repo.UserRepository by FQN before REPO is parsed
    g = build(SERVICE, REPO)
    repo_node = node(g, "type:com.example.repo.UserRepository")
    assert repo_node.kind == "Interface"
    assert repo_node.properties.get("resolved") is True
    assert repo_node not in g.unresolved()


def test_counts_shape():
    g = build(SERVICE, BASE, REPO)
    c = g.counts()
    assert c["node_count"] == len(g.nodes)
    assert "DECLARES" in c["edges_by_type"]
    assert c["nodes_by_kind"].get("Method", 0) >= 2
