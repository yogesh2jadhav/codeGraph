from pathlib import Path

from code_memory.analyzers.dependencies import parse_build_files

FIXTURE = Path(__file__).parents[1] / "fixtures" / "spring_sql_sample"


def test_parse_maven_pom():
    info, warnings = parse_build_files([FIXTURE / "pom.xml"], [], FIXTURE)
    assert warnings == []
    assert info.build_system == "maven"
    assert info.group_id == "com.example"
    assert info.artifact_id == "user-service"
    assert info.version == "1.0.0"
    assert info.java_version == "21"
    assert info.spring_boot_version == "3.3.2"
    assert "PostgreSQL" in info.database_drivers
    assert "Logback" in info.logging_frameworks
    assert "Spring Boot Test" in info.testing_frameworks
    assert any(d["artifact"] == "postgresql" for d in info.dependencies)


def test_parse_gradle(tmp_path: Path):
    (tmp_path / "build.gradle").write_text(
        """
        plugins { id 'org.springframework.boot' version '3.2.0' }
        java { sourceCompatibility = JavaVersion.VERSION_17 }
        dependencies {
            implementation 'org.apache.spark:spark-sql_2.12:3.5.1'
            implementation 'mysql:mysql-connector-java:8.0.33'
            testImplementation 'org.junit.jupiter:junit-jupiter:5.10.0'
        }
        """,
        encoding="utf-8",
    )
    info, warnings = parse_build_files([], [tmp_path / "build.gradle"], tmp_path)
    assert warnings == []
    assert info.build_system == "gradle"
    assert info.java_version == "17"
    assert info.spring_boot_version == "3.2.0"
    assert info.spark_version == "3.5.1"
    assert info.scala_version == "2.12"
    assert "MySQL" in info.database_drivers
    assert "JUnit 5" in info.testing_frameworks


def test_broken_pom_is_tolerated(tmp_path: Path):
    bad = tmp_path / "pom.xml"
    bad.write_text("<project><unclosed>", encoding="utf-8")
    info, warnings = parse_build_files([bad], [], tmp_path)
    assert len(warnings) == 1
    assert info.build_system == "maven"
