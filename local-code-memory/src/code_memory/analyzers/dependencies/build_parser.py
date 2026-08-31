"""Parse Maven and Gradle build files into a :class:`BuildInfo`.

This is intentionally tolerant: build files vary wildly and a parse failure for
one file must never abort a scan. Anything we cannot determine is left as
``None`` / empty rather than guessed. Callers receive ``(BuildInfo, warnings)``.

Maven parsing uses the XML tree. Gradle parsing is regex-based (Groovy/Kotlin
DSLs are not worth a real parser for Phase 1) and only recognises the common
``group:artifact:version`` dependency string form.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from code_memory.models.inventory import BuildInfo

# -- classification tables ------------------------------------------------
_DB_DRIVER_HINTS = {
    "mysql": "MySQL", "mariadb": "MariaDB", "postgresql": "PostgreSQL",
    "ojdbc": "Oracle", "oracle": "Oracle", "mssql-jdbc": "SQL Server",
    "sqljdbc": "SQL Server", "h2": "H2", "hsqldb": "HSQLDB",
    "sqlite-jdbc": "SQLite", "db2": "DB2", "snowflake-jdbc": "Snowflake",
    "redshift-jdbc": "Redshift",
}
_LOGGING_HINTS = {
    "logback": "Logback", "log4j-core": "Log4j2", "log4j-api": "Log4j2",
    "log4j2": "Log4j2", "log4j": "Log4j", "slf4j-api": "SLF4J",
    "slf4j": "SLF4J", "tinylog": "tinylog",
}
_TESTING_HINTS = {
    "junit-jupiter": "JUnit 5", "junit-vintage": "JUnit (vintage)",
    "junit": "JUnit 4", "testng": "TestNG", "mockito": "Mockito",
    "spring-boot-starter-test": "Spring Boot Test", "assertj": "AssertJ",
    "spark-testing-base": "spark-testing-base", "spark-fast-tests": "spark-fast-tests",
}

_SCALA_SUFFIX_RE = re.compile(r"_(2\.1[0-3]|3)(?:\.\d+)?$")
_GRADLE_DEP_RE = re.compile(
    r"""(?:implementation|api|compile|testImplementation|testCompile|
        runtimeOnly|compileOnly|annotationProcessor|kapt)
        \s*[(\s]\s*['"]([\w.\-]+):([\w.\-]+)(?::([\w.\-${}]+))?['"]""",
    re.VERBOSE,
)


def parse_build_files(pom_paths: list[Path], gradle_paths: list[Path],
                      root: Path) -> tuple[BuildInfo, list[str]]:
    warnings: list[str] = []
    info = BuildInfo()

    have_maven = bool(pom_paths)
    have_gradle = any(p.name.startswith("build.gradle") for p in gradle_paths)
    info.build_system = (
        "mixed" if have_maven and have_gradle
        else "maven" if have_maven
        else "gradle" if have_gradle
        else "unknown"
    )

    for pom in pom_paths:
        try:
            _parse_pom(pom, root, info)
        except Exception as exc:  # tolerant: record and continue
            warnings.append(f"failed to parse {pom}: {exc}")

    for gradle in gradle_paths:
        try:
            _parse_gradle(gradle, info)
        except Exception as exc:
            warnings.append(f"failed to parse {gradle}: {exc}")

    _dedupe(info)
    return info, warnings


# -- Maven -------------------------------------------------------------
def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_pom(path: Path, root: Path, info: BuildInfo) -> None:
    tree = ET.parse(path)
    proj = tree.getroot()
    children = {_strip_ns(c.tag): c for c in proj}

    # Coordinates - only take them from the root module POM.
    is_root = path.parent.resolve() == root.resolve()
    if is_root:
        info.artifact_id = info.artifact_id or _text(children.get("artifactId"))
        info.version = info.version or _text(children.get("version"))
        info.group_id = info.group_id or _text(children.get("groupId"))

    props: dict[str, str] = {}
    if "properties" in children:
        for p in children["properties"]:
            props[_strip_ns(p.tag)] = (p.text or "").strip()

    # Parent (commonly spring-boot-starter-parent -> Spring Boot version).
    if "parent" in children:
        pc = {_strip_ns(c.tag): _text(c) for c in children["parent"]}
        if not info.group_id and is_root:
            info.group_id = pc.get("groupId")
        if not info.version and is_root:
            info.version = pc.get("version")
        if "spring-boot-starter-parent" in (pc.get("artifactId") or ""):
            info.spring_boot_version = info.spring_boot_version or pc.get("version")

    # Java version from common property spellings.
    for key in ("java.version", "maven.compiler.release",
                "maven.compiler.target", "maven.compiler.source"):
        if props.get(key) and not info.java_version:
            info.java_version = props[key]

    if props.get("spring-boot.version"):
        info.spring_boot_version = info.spring_boot_version or props["spring-boot.version"]

    # Dependencies (+ dependencyManagement, which carries BOM versions).
    for container_tag in ("dependencies", "dependencyManagement"):
        container = children.get(container_tag)
        if container is None:
            continue
        for dep in container.iter():
            if _strip_ns(dep.tag) != "dependency":
                continue
            fields = {_strip_ns(c.tag): _text(c) for c in dep}
            group = fields.get("groupId", "")
            artifact = fields.get("artifactId", "")
            version = _resolve_prop(fields.get("version"), props)
            _classify_dependency(group, artifact, version, info)


def _text(el) -> str | None:
    if el is None:
        return None
    return (el.text or "").strip() or None


def _resolve_prop(value: str | None, props: dict[str, str]) -> str | None:
    if not value:
        return value
    m = re.fullmatch(r"\$\{([^}]+)\}", value.strip())
    if m:
        return props.get(m.group(1), value)
    return value


# -- Gradle ----------------------------------------------------------
def _parse_gradle(path: Path, info: BuildInfo) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")

    if path.name.startswith("gradle.properties") or path.name == "gradle.properties":
        for line in text.splitlines():
            line = line.strip()
            if line.startswith(("javaVersion", "java.version")) and "=" in line:
                info.java_version = info.java_version or line.split("=", 1)[1].strip()
        return

    for key in ("sourceCompatibility", "targetCompatibility",
                "languageVersion", "JavaLanguageVersion.of"):
        m = re.search(rf"{re.escape(key)}\s*[=(]\s*['\"]?(?:JavaVersion\.VERSION_)?"
                      r"([0-9._]+)", text)
        if m and not info.java_version:
            info.java_version = m.group(1).replace("_", ".")

    m = re.search(r"id\s*['\"]org\.springframework\.boot['\"]\s*\)?\s*"
                  r"version\s*['\"]([\w.\-]+)['\"]", text)
    if m:
        info.spring_boot_version = info.spring_boot_version or m.group(1)

    for group, artifact, version in _GRADLE_DEP_RE.findall(text):
        _classify_dependency(group, artifact, version or None, info)


# -- shared classification ----------------------------------------
def _classify_dependency(group: str, artifact: str, version: str | None,
                         info: BuildInfo) -> None:
    group = (group or "").strip()
    artifact = (artifact or "").strip()
    if not artifact:
        return

    info.dependencies.append(
        {"group": group, "artifact": artifact, "version": version or ""}
    )

    lower = artifact.lower()

    if group == "org.apache.spark" or lower.startswith("spark-"):
        if version:
            info.spark_version = info.spark_version or version
        sm = _SCALA_SUFFIX_RE.search(artifact)
        if sm:
            info.scala_version = info.scala_version or sm.group(1)

    if group.startswith("org.scala-lang") and artifact == "scala-library" and version:
        info.scala_version = info.scala_version or ".".join(version.split(".")[:2])

    if (group == "org.springframework.boot"
            and artifact == "spring-boot-starter-parent" and version):
        info.spring_boot_version = info.spring_boot_version or version

    for hint, label in _DB_DRIVER_HINTS.items():
        if hint in lower:
            info.database_drivers.append(label)
    for hint, label in _LOGGING_HINTS.items():
        if hint in lower:
            info.logging_frameworks.append(label)
    for hint, label in _TESTING_HINTS.items():
        if hint in lower:
            info.testing_frameworks.append(label)


def _dedupe(info: BuildInfo) -> None:
    def uniq(seq: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for item in seq:
            seen.setdefault(item, None)
        return list(seen)

    info.database_drivers = uniq(info.database_drivers)
    info.logging_frameworks = uniq(info.logging_frameworks)
    info.testing_frameworks = uniq(info.testing_frameworks)

    seen_dep: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, str]] = []
    for dep in info.dependencies:
        key = (dep["group"], dep["artifact"], dep["version"])
        if key not in seen_dep:
            seen_dep.add(key)
            deduped.append(dep)
    info.dependencies = sorted(deduped, key=lambda d: (d["group"], d["artifact"]))
