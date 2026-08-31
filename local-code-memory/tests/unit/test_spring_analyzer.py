from code_memory.analyzers.spring import analyze_spring
from code_memory.graph import build_graph
from code_memory.models.graph import Confidence
from code_memory.parsers.java import parse_java_source

CONTROLLER = b"""
package app.web;
import app.svc.UserService;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/users")
public class UserController {
    private final UserService service;
    public UserController(UserService service) { this.service = service; }

    @GetMapping("/{id}")
    public User get(@PathVariable String id) { return service.find(id); }

    @PostMapping
    public User create(@RequestBody User u) { return service.create(u); }

    @RequestMapping(value = "/search", method = RequestMethod.GET)
    public User search() { return null; }
}
"""
SERVICE = b"""
package app.svc;
import app.repo.UserRepository;
import org.springframework.stereotype.Service;

@Service
public class UserService {
    private final UserRepository repo;
    public UserService(UserRepository repo) { this.repo = repo; }
    public User find(String id) { return repo.findById(id); }
    public User create(User u) { return repo.save(u); }
}
"""
REPO = b"""
package app.repo;
import org.springframework.stereotype.Repository;
@Repository
public interface UserRepository {
    User findById(String id);
    User save(User u);
}
"""
ADVICE = b"""
package app.web;
import org.springframework.web.bind.annotation.*;
@RestControllerAdvice
public class Errors {
    @ExceptionHandler(IllegalStateException.class)
    public String onBad(IllegalStateException e) { return "bad"; }
}
"""
CONFIG = b"""
package app.cfg;
import org.springframework.context.annotation.*;
@Configuration
public class AppConfig {
    @Bean
    public Clock clock() { return null; }
}
"""


def analyze(*sources):
    parsed = [parse_java_source(f"F{i}.java", s) for i, s in enumerate(sources)]
    g = build_graph(parsed)
    model = analyze_spring(parsed, g)
    return g, model


def test_stereotypes_detected():
    g, m = analyze(CONTROLLER, SERVICE, REPO, ADVICE, CONFIG)
    assert m.stereotypes == {
        "app.web.UserController": "RestController",
        "app.svc.UserService": "Service",
        "app.repo.UserRepository": "Repository",
        "app.web.Errors": "ControllerAdvice",
        "app.cfg.AppConfig": "Configuration",
    }
    assert g.get("type:app.svc.UserService").properties["spring_stereotype"] == "Service"


def test_endpoints_paths_and_methods():
    g, m = analyze(CONTROLLER, SERVICE, REPO)
    eps = {(e["http_method"], e["path"]) for e in m.endpoints}
    assert eps == {("GET", "/api/users/{id}"),
                   ("POST", "/api/users"),
                   ("GET", "/api/users/search")}
    # graph edges
    exposes = {(e.src, e.dst) for e in g.edges if e.type == "EXPOSES"}
    assert ("type:app.web.UserController", "endpoint:GET /api/users/{id}") in exposes
    mapped = {(e.src, e.dst) for e in g.edges if e.type == "MAPPED_TO"}
    assert ("endpoint:POST /api/users",
            "method:app.web.UserController#create(User)") in mapped


def test_injections_between_components():
    g, m = analyze(CONTROLLER, SERVICE, REPO)
    injects = {(e.src, e.dst, e.confidence) for e in g.edges if e.type == "INJECTS"}
    assert ("type:app.web.UserController", "type:app.svc.UserService",
            Confidence.HIGH) in injects
    assert ("type:app.svc.UserService", "type:app.repo.UserRepository",
            Confidence.HIGH) in injects


def test_bean_and_exception_handler_flags():
    g, m = analyze(CONFIG, ADVICE)
    assert m.beans == ["app.cfg.AppConfig#clock()"]
    assert g.get("method:app.cfg.AppConfig#clock()").properties.get("bean") is True

    assert m.exception_handlers == ["app.web.Errors#onBad(IllegalStateException)"]
    handles = {(e.src, e.dst) for e in g.edges if e.type == "HANDLES"}
    assert ("method:app.web.Errors#onBad(IllegalStateException)",
            "type:java.lang.IllegalStateException") in handles


def test_non_spring_project_is_inert():
    g, m = analyze(b"package a; public class Plain { void m() {} }")
    assert not m.is_spring()
    assert m.counts()["spring_detected"] is False


def test_endpoint_flow_uses_call_graph():
    g, m = analyze(CONTROLLER, SERVICE, REPO)
    # controller.get -> service.find -> repo.findById  (all resolved by Phase 3)
    from code_memory.graph import queries
    callees = {c["id"] for c in queries.find_callees(
        g, "method:app.web.UserController#get(String)")}
    assert "method:app.svc.UserService#find(String)" in callees
