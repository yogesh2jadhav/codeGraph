from code_memory.models.code import EntityKind, ParseStatus
from code_memory.parsers.java import parse_java_source

SAMPLE = b'''
package com.example.demo;

import java.util.List;
import static org.junit.Assert.assertEquals;

@Service
@RequestMapping("/users")
public abstract class UserService<T extends Number> extends BaseService implements Api, Closeable {

    @Autowired
    private final UserRepository repo;
    private static int COUNT = 0, LIMIT = 10;

    public UserService(UserRepository repo) { this.repo = repo; }

    @Override
    protected <R> R createUser(String name, int... tags) throws IOException {
        return null;
    }

    enum Status { ACTIVE, DISABLED }

    static class Inner {
        void ping() {}
    }
}
'''


def parse():
    return parse_java_source("src/main/java/com/example/demo/UserService.java", SAMPLE)


def test_package_and_imports():
    pf = parse()
    assert pf.status == ParseStatus.SUCCESS
    assert pf.package == "com.example.demo"
    assert [(i.fqn, i.static, i.wildcard) for i in pf.imports] == [
        ("java.util.List", False, False),
        ("org.junit.Assert.assertEquals", True, False),
    ]


def test_top_type():
    t = parse().types[0]
    assert t.name == "UserService"
    assert t.kind == EntityKind.CLASS
    assert t.fqn == "com.example.demo.UserService"
    assert "abstract" in t.modifiers and "public" in t.modifiers
    assert t.extends == ["BaseService"]
    assert set(t.implements) == {"Api", "Closeable"}
    assert t.type_parameters and "T" in t.type_parameters[0]
    assert {a.name for a in t.annotations} == {"Service", "RequestMapping"}
    assert next(a for a in t.annotations
                if a.name == "RequestMapping").arguments_text == '("/users")'
    # location spans the leading annotations too
    assert t.location.line_start == 7


def test_fields():
    t = parse().types[0]
    by_name = {f.name: f for f in t.fields}
    assert by_name["repo"].type_text == "UserRepository"
    assert "final" in by_name["repo"].modifiers
    assert {a.name for a in by_name["repo"].annotations} == {"Autowired"}
    # two declarators on one statement
    assert "COUNT" in by_name and "LIMIT" in by_name
    assert by_name["COUNT"].type_text == "int"


def test_methods_and_params():
    t = parse().types[0]
    ctor = next(m for m in t.methods if m.kind == EntityKind.CONSTRUCTOR)
    assert ctor.name == "UserService"
    assert [p.type_text for p in ctor.parameters] == ["UserRepository"]

    m = next(m for m in t.methods if m.name == "createUser")
    assert m.kind == EntityKind.METHOD
    assert m.return_type == "R"
    assert m.throws == ["IOException"]
    assert m.parameters[0].type_text == "String"
    assert m.parameters[1].varargs and m.parameters[1].type_text == "int..."
    assert m.signature == "createUser(String,int...)"
    assert m.fqn == "com.example.demo.UserService#createUser(String,int...)"
    assert {a.name for a in m.annotations} == {"Override"}


def test_nested_types():
    t = parse().types[0]
    nested = {n.name: n for n in t.nested}
    assert set(nested) == {"Status", "Inner"}
    assert nested["Status"].kind == EntityKind.ENUM
    assert nested["Status"].fqn == "com.example.demo.UserService.Status"
    assert {f.name for f in nested["Status"].fields} == {"ACTIVE", "DISABLED"}
    assert nested["Inner"].methods[0].name == "ping"


def test_record_components_become_fields():
    pf = parse_java_source("R.java", b"package p; public record Point(int x, int y) {}")
    rec = pf.types[0]
    assert rec.kind == EntityKind.RECORD
    assert [f.name for f in rec.fields] == ["x", "y"]
    assert rec.fields[0].type_text == "int"


def test_broken_file_is_partial_not_fatal():
    pf = parse_java_source("B.java", b"package p; class B { void m( { }")
    assert pf.status == ParseStatus.PARTIAL
    assert pf.types and pf.types[0].name == "B"
