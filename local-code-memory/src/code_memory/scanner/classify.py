"""Classify a repository-relative path into a :class:`FileKind`.

Pure and side-effect free so it is trivially unit tested. Ordering matters:
more specific rules are checked before generic ones.
"""

from __future__ import annotations

from code_memory.models.inventory import FileKind

_TEXT_EXTS = {
    ".java", ".scala", ".kt", ".xml", ".yml", ".yaml", ".properties", ".sql",
    ".json", ".conf", ".md", ".adoc", ".rst", ".txt", ".sh", ".bash", ".py",
    ".gradle", ".toml", ".cfg", ".ini", ".dockerfile",
}


def is_text_ext(name: str) -> bool:
    dot = name.rfind(".")
    return dot != -1 and name[dot:].lower() in _TEXT_EXTS


def classify(rel_path: str) -> FileKind:
    parts = rel_path.replace("\\", "/").split("/")
    name = parts[-1]
    lower = name.lower()
    p = "/" + "/".join(parts) + "/"

    in_test_tree = "/src/test/" in p or "/test/" in p or "/tests/" in p

    # -- Java / Scala sources -----------------------------------------
    if lower.endswith(".java"):
        if in_test_tree or lower.endswith(("test.java", "tests.java", "it.java",
                                           "spec.java")):
            return FileKind.JAVA_TEST
        return FileKind.JAVA_MAIN
    if lower.endswith((".scala", ".sc")):
        return FileKind.SCALA

    # -- build files ------------------------------------------------
    if lower == "pom.xml":
        return FileKind.MAVEN_POM
    if lower in ("build.gradle", "build.gradle.kts"):
        return FileKind.GRADLE_BUILD
    if lower in ("settings.gradle", "settings.gradle.kts"):
        return FileKind.GRADLE_SETTINGS
    if lower == "gradle.properties":
        return FileKind.GRADLE_PROPERTIES

    # -- app / spark configuration --------------------------------
    if (lower.startswith("application") or lower.startswith("bootstrap")) and \
            lower.endswith((".yml", ".yaml", ".properties")):
        return FileKind.APP_CONFIG
    if "spark" in lower and lower.endswith((".conf", ".properties")):
        return FileKind.SPARK_CONFIG
    if lower in ("spark-defaults.conf", "spark-env.sh"):
        return FileKind.SPARK_CONFIG

    # -- SQL ------------------------------------------------------
    if lower.endswith(".sql"):
        return FileKind.SQL

    # -- docker -------------------------------------------------
    if lower == "dockerfile" or lower.endswith(".dockerfile") or \
            lower.startswith("dockerfile.") or lower.startswith("docker-compose"):
        return FileKind.DOCKER

    # -- docs -------------------------------------------------
    if lower.startswith("readme"):
        return FileKind.README
    if lower.endswith((".md", ".adoc", ".rst")):
        return FileKind.DOC

    # -- scripts --------------------------------------------
    if lower.endswith((".sh", ".bash", ".ps1")) or "/scripts/" in p or "/bin/" in p:
        return FileKind.SCRIPT

    # -- resources by source tree --------------------------
    if "/src/test/resources/" in p:
        return FileKind.TEST_RESOURCE
    if "/src/main/resources/" in p:
        return FileKind.RESOURCE

    if lower.endswith((".xml", ".yml", ".yaml", ".properties", ".json", ".toml",
                       ".conf", ".ini", ".cfg")):
        return FileKind.BUILD_OTHER

    return FileKind.OTHER
