from code_memory.graph import build_graph, queries
from code_memory.models.graph import Confidence
from code_memory.parsers.java import parse_java_source

REPO = b"""
package app.repo;
public interface UserRepository {
    User findById(String id);
    User save(User u);
}
"""
USER = b"package app.repo; public class User { public String name() { return null; } }"
UTIL = b"package app.util; public class Ids { public static String next() { return null; } }"
SERVICE = b"""
package app.svc;
import app.repo.UserRepository;
import app.repo.User;
import app.util.Ids;
public class UserService extends Base {
    private final UserRepository repo;
    public UserService(UserRepository repo) { this.repo = repo; }

    public User create(String name) {
        User u = new User();
        String id = Ids.next();
        repo.save(u);
        return repo.findById(id);
    }

    public void audit() {
        helper();
        super.baseOp();
        try { create("x"); } catch (RuntimeException e) { }
    }

    private void helper() {}
}
"""
BASE = b"package app.svc; public class Base { public void baseOp() {} }"


def graph():
    parsed = [parse_java_source(f"F{i}.java", s)
              for i, s in enumerate((REPO, USER, UTIL, SERVICE, BASE))]
    return build_graph(parsed)


def calls_from(g, method_fqn):
    return {(e.dst, e.confidence) for e in g.edges
            if e.type == "CALLS" and e.src == f"method:{method_fqn}"}


def test_call_via_field_type_resolved_high():
    g = graph()
    got = calls_from(g, "app.svc.UserService#create(String)")
    assert ("method:app.repo.UserRepository#save(User)", Confidence.HIGH) in got
    assert ("method:app.repo.UserRepository#findById(String)", Confidence.HIGH) in got


def test_constructor_and_static_and_local_calls():
    g = graph()
    got = {d for d, _ in calls_from(g, "app.svc.UserService#create(String)")}
    # new User() -> constructor link is absent (User has only the default ctor,
    # which we don't synthesise) but USES_TYPE/CREATES must exist
    creates = {e.dst for e in g.edges if e.type == "CREATES"
               and e.src == "method:app.svc.UserService#create(String)"}
    assert "type:app.repo.User" in creates
    # static call Ids.next()
    assert "method:app.util.Ids#next()" in got


def test_implicit_this_and_super_calls():
    g = graph()
    got = {d for d, _ in calls_from(g, "app.svc.UserService#audit()")}
    assert "method:app.svc.UserService#helper()" in got      # implicit this
    assert "method:app.svc.Base#baseOp()" in got             # super.baseOp()


def test_unresolved_external_call_collapsed():
    src = b"""
    package a;
    public class C {
        void m(java.util.List list) { list.add(1); list.clear(); }
    }
    """
    g = build_graph([parse_java_source("C.java", src)])
    ext = [e for e in g.edges if e.type == "CALLS"]
    assert all(e.confidence == Confidence.UNKNOWN for e in ext)
    # receiver 'list' is a param of type java.util.List -> guess uses the type
    assert {e.dst for e in ext} == {"extmethod:java.util.List.add",
                                    "extmethod:java.util.List.clear"}
    assert any(n.properties.get("external") for n in g.unresolved())


def test_catches_and_returns_and_uses_type():
    g = graph()
    catches = {e.dst for e in g.edges if e.type == "CATCHES"}
    assert "type:java.lang.RuntimeException" in catches

    returns = {(e.src, e.dst) for e in g.edges if e.type == "RETURNS_TYPE"}
    assert ("method:app.svc.UserService#create(String)", "type:app.repo.User") in returns

    uses = {e.dst for e in g.edges if e.type == "USES_TYPE"
            and e.src == "method:app.svc.UserService#create(String)"}
    assert "type:app.repo.User" in uses


def test_overrides_edge():
    child = b"""
    package o;
    import o.P;
    public class Child extends P {
        @Override public String describe() { return null; }
    }
    """
    parent = b"package o; public class P { public String describe() { return null; } }"
    g = build_graph([parse_java_source("Child.java", child),
                     parse_java_source("P.java", parent)])
    ov = [e for e in g.edges if e.type == "OVERRIDES"]
    assert len(ov) == 1
    assert ov[0].src == "method:o.Child#describe()"
    assert ov[0].dst == "method:o.P#describe()"
    assert ov[0].confidence == Confidence.HIGH


def test_queries_callers_callees_and_paths():
    g = graph()
    save_id = "method:app.repo.UserRepository#save(User)"
    callers = {c["id"] for c in queries.find_callers(g, save_id)}
    assert "method:app.svc.UserService#create(String)" in callers

    audit = "method:app.svc.UserService#audit()"
    create = "method:app.svc.UserService#create(String)"
    paths = queries.call_paths(g, audit, save_id)
    assert [audit, create, save_id] in paths
    assert create in queries.transitive_callers(g, save_id)
