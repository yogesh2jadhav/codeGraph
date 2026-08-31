import pytest

from code_memory.models.inventory import FileKind
from code_memory.scanner.classify import classify


@pytest.mark.parametrize("path,expected", [
    ("src/main/java/com/example/UserService.java", FileKind.JAVA_MAIN),
    ("src/test/java/com/example/UserServiceTest.java", FileKind.JAVA_TEST),
    ("app/src/test/java/Foo.java", FileKind.JAVA_TEST),
    ("core/FooIT.java", FileKind.JAVA_TEST),
    ("etl/src/main/scala/Job.scala", FileKind.SCALA),
    ("pom.xml", FileKind.MAVEN_POM),
    ("build.gradle.kts", FileKind.GRADLE_BUILD),
    ("settings.gradle", FileKind.GRADLE_SETTINGS),
    ("gradle.properties", FileKind.GRADLE_PROPERTIES),
    ("src/main/resources/application.yml", FileKind.APP_CONFIG),
    ("src/main/resources/application-prod.properties", FileKind.APP_CONFIG),
    ("conf/spark-defaults.conf", FileKind.SPARK_CONFIG),
    ("src/main/resources/db/schema.sql", FileKind.SQL),
    ("Dockerfile", FileKind.DOCKER),
    ("docker-compose.yml", FileKind.DOCKER),
    ("README.md", FileKind.README),
    ("docs/design.md", FileKind.DOC),
    ("scripts/run.sh", FileKind.SCRIPT),
    ("src/main/resources/logback.xml", FileKind.RESOURCE),
    ("src/test/resources/fixture.json", FileKind.TEST_RESOURCE),
    ("random/notes.txt", FileKind.OTHER),
])
def test_classify(path, expected):
    assert classify(path) == expected
