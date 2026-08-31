# Local Code Memory & Local LLM Coding Assistant
## Detailed Implementation Plan for Claude Code

**Version:** 1.0  
**Target:** Java backend repositories  
**Supported application styles:**  
- Java + Apache Spark + SQL / ETL
- Java + Spring Boot + SQL / ETL
- Mixed Java backend repositories

**Primary objective:** Build a completely local system that deeply scans a Java project, creates a persistent machine-readable "Code Memory", and produces a compact set of Markdown/JSON context artifacts that can be supplied to a local coding LLM. The system must help a local LLM answer questions such as:

- Where should I modify the code?
- What classes/methods are affected?
- What calls this method?
- What does this method call?
- What is the end-to-end flow?
- Which SQL/table/configuration is involved?
- Where should logging be added?
- What is likely to break if I change this method?
- Which tests should be changed or added?
- How should I implement a requested feature?
- What files should an AI coding agent inspect before editing?

---

# 1. Executive Design Decision

Do **not** build this as a vector-only RAG system.

The core system must use three complementary representations:

1. **Code Graph / Knowledge Graph** — authoritative relationships between code entities.
2. **Vector Index** — semantic retrieval of relevant code and documentation.
3. **Generated Markdown Context Pack** — portable human/LLM-readable representation that can be shared directly with a local LLM/coding agent.

The raw source code remains the ultimate ground truth.

Recommended architecture:

```text
Java Repository
      |
      v
Deep Scanner
      |
      +--> Java AST / symbols
      +--> Call graph
      +--> inheritance / implementation
      +--> imports / dependencies
      +--> annotations
      +--> control-flow information
      +--> data-flow information
      +--> SQL analysis
      +--> Spark analysis
      +--> Spring analysis
      +--> configuration analysis
      +--> logging analysis
      +--> tests
      |
      v
Normalized Code Knowledge Model
      |
      +-------------------+
      |                   |
      v                   v
Graph DB             Vector DB
      |                   |
      +---------+---------+
                |
                v
        Hybrid Retrieval
                |
        +-------+--------+
        |                |
        v                v
Context Generator   Local LLM
        |
        v
Markdown Context Pack
```

---

# 2. Primary Product Goal

The first version is **not** an autonomous coding agent.

The first version is a **Code Memory Builder + Context Generator**.

Given:

```bash
code-memory scan /path/to/java-project
```

it should:

1. scan the repository;
2. understand its structure;
3. extract code entities and relationships;
4. build a graph;
5. build a vector index;
6. identify application architecture;
7. identify important execution flows;
8. identify SQL and database relationships;
9. identify Spark/Spring-specific behavior;
10. identify configuration;
11. identify logging;
12. identify tests;
13. generate Markdown context files;
14. generate machine-readable JSON;
15. expose a local query API;
16. optionally send retrieved context to a local LLM.

The output should be usable even if the LLM is completely disconnected from the scanner.

---

# 3. Important Design Principle: Two Output Modes

The system must support BOTH:

## Mode A — Graph/DB mode

For the local application itself:

```text
Question
  -> Graph retrieval
  -> Vector retrieval
  -> Source retrieval
  -> Context builder
  -> Local LLM
```

## Mode B — Markdown context mode

For external/local coding tools:

```text
Repository
  -> scanner
  -> generated .md files
  -> local coding LLM / coding agent
```

This is important because the generated Markdown becomes a portable "memory snapshot".

The user should be able to copy/share:

```text
.code-memory/context/
```

with a local coding assistant.

---

# 4. Recommended Technology Stack

## 4.1 Primary code analysis

### Joern

Use Joern as a major source of deep code-property information.

Investigate and integrate:

- AST
- call relationships
- methods
- classes
- parameters
- types
- control flow
- data flow where available
- source locations

Joern should be treated as an analysis engine, not necessarily as the application's final knowledge database.

## 4.2 Java semantic analysis

Add a Java-specific semantic layer using one or more of:

- Eclipse JDT
- JavaParser
- OpenRewrite

OpenRewrite should be evaluated especially for:

- method usages
- class hierarchy
- Java source structure
- control-flow analysis
- transformations later in the project

Do not depend on a single parser if that prevents accurate resolution.

## 4.3 Optional static analysis

Evaluate CodeQL for additional Java/Spring/JDBC/JPA analysis.

CodeQL is optional for V1 and should not become a hard runtime dependency unless it materially improves accuracy.

## 4.4 Graph database

### Recommended first choice: Neo4j

Use Neo4j for the application's normalized Code Knowledge Graph.

Why:

- mature graph query language;
- easy visualization;
- excellent for relationship exploration;
- good fit for code entities and dependencies;
- useful for debugging the generated memory graph.

The project must hide the graph implementation behind a repository interface so that another graph backend can be added later.

Do NOT make the entire application depend directly on Neo4j queries everywhere.

Create:

```text
GraphRepository
Neo4jGraphRepository
```

## 4.5 Vector database

### Recommended first choice: Qdrant

Use Qdrant for semantic code retrieval.

Store embeddings for:

- classes
- methods
- constructors
- REST endpoints
- Spark jobs
- Spark transformations
- SQL statements
- repository queries
- exception handlers
- configuration descriptions
- documentation
- important code blocks

Do not blindly embed entire Java files.

## 4.6 Metadata database

### Recommended: SQLite for V1

Use SQLite for:

- scan metadata
- project metadata
- file inventory
- hashes
- scanner version
- graph build version
- embedding version
- scan timestamps
- generated context manifest
- incremental scan state
- errors/warnings
- model/index metadata

Keep this independent from Neo4j and Qdrant.

PostgreSQL can be supported later.

---

# 5. Local LLM Strategy

The system must not depend on paid APIs.

## Primary coding model recommendation

### Qwen3-Coder 30B A3B Instruct

Use the local Ollama build:

```bash
ollama run qwen3-coder:30b
```

It is a strong fit for repository-scale coding and long-context work.

The official Ollama listing reports approximately 30B total parameters, 3.3B activated parameters, 256K native context, and a roughly 19GB Q4 model footprint.

Use the model through an abstraction:

```text
LLMProvider
  |
  +-- OllamaProvider
  +-- LlamaCppProvider (future)
```

Do not hard-code Ollama throughout the application.

## Important hardware rule

Do not assume that a 30B model is practical on every laptop.

The project must support configurable models:

```yaml
llm:
  provider: ollama
  model: qwen3-coder:30b
  context_window: 32768
  temperature: 0.1
```

Also allow a smaller coding model for lower-memory machines.

Suggested model tiers:

```text
High-memory:
    Qwen3-Coder 30B

Medium-memory:
    smaller Qwen coder model

Very limited hardware:
    smaller 7B/14B coding model
```

The exact model must remain configurable.

---

# 6. Local Embedding Model

Do not use the coding LLM itself for embeddings.

Create a separate:

```text
EmbeddingProvider
```

with local models.

Recommended initial candidates should be benchmarked against the user's Java code:

- Qwen embedding family
- BGE embedding family
- other strong local code/text embedding models

The benchmark should measure:

- method retrieval
- class retrieval
- SQL retrieval
- business terminology retrieval
- error-description retrieval

The selected embedding model must be configurable.

---

# 7. Reranker

Add a local reranker interface:

```text
Reranker
  |
  +-- LocalCrossEncoderReranker
```

This should be optional in early development.

Pipeline:

```text
Vector DB top 30
      |
      v
Reranker
      |
      v
Top 5-10
```

This can materially improve retrieval quality.

---

# 8. Code Memory Graph Schema

Create a normalized graph schema.

## Nodes

At minimum:

```text
Project
Module
Directory
Package
SourceFile
Class
Interface
Enum
Annotation
Method
Constructor
Field
Parameter
LocalVariable
Exception
Test
Endpoint
Configuration
ConfigProperty
SQLStatement
Database
Table
Column
SparkJob
SparkTransformation
SparkAction
Dependency
ExternalService
LogStatement
BuildArtifact
```

## Relationships

At minimum:

```text
CONTAINS
DECLARES
IMPORTS
EXTENDS
IMPLEMENTS
ANNOTATED_WITH
CALLS
OVERRIDES
USES_TYPE
USES_FIELD
READS_FIELD
WRITES_FIELD
ACCEPTS_PARAMETER
RETURNS_TYPE
THROWS
CATCHES
CREATES
DEPENDS_ON
TESTED_BY
EXPOSES
MAPPED_TO
READS_CONFIG
USES_CONFIG
EXECUTES_SQL
READS_TABLE
WRITES_TABLE
CALLS_EXTERNAL_SERVICE
LOGS
PART_OF
TRANSFORMS
FLOWS_TO
```

Every relationship should preserve source evidence where possible:

```text
source_file
line_start
line_end
confidence
scanner
scanner_version
```

---

# 9. Source Location Is Mandatory

Every important graph node must have:

```text
file_path
relative_path
line_start
line_end
symbol
fully_qualified_name
```

Example:

```json
{
  "type": "method",
  "fully_qualified_name": "com.example.UserService.createUser",
  "file": "src/main/java/com/example/UserService.java",
  "line_start": 82,
  "line_end": 126
}
```

This is critical because the final LLM must be able to answer:

```text
Change:
UserService.java:94
```

rather than merely:

```text
Change UserService.
```

---

# 10. Project Inventory

The scanner must create a complete repository inventory.

Detect:

```text
src/main/java
src/test/java
src/main/resources
pom.xml
build.gradle
settings.gradle
application.yml
application.properties
Spark configuration
SQL files
Docker files
README files
configuration files
scripts
test resources
```

Also detect:

```text
Maven
Gradle
Java version
Spring Boot version
Spark version
Scala dependencies
database drivers
logging framework
testing framework
```

Generate:

```text
00_project_overview.md
```

---

# 11. Architecture Extraction

Automatically infer:

```text
Controller/API layer
Service layer
Repository/DAO layer
Domain/model layer
Configuration layer
Utility layer
ETL/Spark layer
Integration layer
```

Do not assume standard architecture.

Mark inferred architecture with confidence:

```text
architecture:
  service_layer:
    confidence: 0.91
```

The generated context must distinguish:

```text
Observed
Inferred
Unknown
```

This prevents the LLM from treating guesses as facts.

---

# 12. Java + Spring Boot Analysis

For Spring Boot projects analyze:

- @SpringBootApplication
- @RestController
- @Controller
- @Service
- @Repository
- @Component
- @Configuration
- @Bean
- dependency injection
- @Autowired
- constructor injection
- @RequestMapping
- @GetMapping
- @PostMapping
- @PutMapping
- @DeleteMapping
- exception handlers
- filters
- interceptors
- scheduled jobs
- configuration properties
- profiles

Generate an endpoint graph:

```text
HTTP Endpoint
    |
    v
Controller Method
    |
    v
Service Method
    |
    v
Repository
    |
    v
SQL
    |
    v
Table
```

---

# 13. Java + Spark Analysis

For Spark/ETL projects analyze:

- SparkSession
- SparkContext
- Dataset
- DataFrame
- RDD
- transformations
- actions
- map
- flatMap
- filter
- groupBy
- join
- union
- repartition
- coalesce
- cache
- persist
- collect
- save
- write
- read
- SQL execution
- UDFs
- partition-related behavior

Attempt to reconstruct:

```text
Input
  -> transformation
  -> transformation
  -> join
  -> aggregation
  -> output
```

Also identify:

- input tables
- output tables
- input files
- output files
- SQL
- job entrypoints
- batch boundaries

Generate:

```text
spark_pipeline.md
spark_jobs.md
spark_data_flow.md
```

---

# 14. SQL Analysis

SQL must be a first-class entity.

Extract SQL from:

- Java strings
- JDBC calls
- MyBatis
- JPA/native queries
- repository annotations
- SQL files
- Spark SQL
- configuration

Identify:

```text
SELECT
INSERT
UPDATE
DELETE
MERGE
JOIN
WHERE
GROUP BY
ORDER BY
CTE
subqueries
table names
column names
```

Build:

```text
Method
   |
   v
SQL
   |
   +--> READS --> Table A
   |
   +--> READS --> Table B
   |
   +--> WRITES -> Table C
```

SQL parsing should be separated into its own module.

---

# 15. Dependency Analysis

Parse:

```text
pom.xml
build.gradle
gradle.lockfile
```

Build:

```text
Project
  -> dependency
  -> version
  -> scope
  -> transitive dependency where available
```

Generate:

```text
dependencies.md
```

Also identify dependency usage in source code.

---

# 16. Configuration Memory

Configuration is often missed by code RAG systems.

Scan:

```text
application.properties
application.yml
application-*.yml
application-*.properties
bootstrap.*
environment templates
Docker configuration
Maven profiles
Gradle properties
Spark configuration
```

Build relationships:

```text
ConfigProperty
      |
      v
Class/Method using property
```

Example:

```text
app.user.timeout
      |
      v
UserService.createUser()
```

Generate:

```text
configuration.md
configuration_usage.md
```

Never store actual secrets in generated context.

Detect and redact:

```text
password
secret
token
api_key
private_key
credentials
```

---

# 17. Logging Analysis

Since the larger goal includes AI-assisted logging, make logging first-class.

Detect:

```text
SLF4J
Logback
Log4j
java.util.logging
logger.info
logger.debug
logger.warn
logger.error
logger.trace
```

For every logging statement record:

```text
file
line
class
method
level
message/template
exception included
variables included
```

Also identify important methods with:

```text
no logging
```

Potential candidates:

- external API calls
- DB operations
- Spark job boundaries
- batch start/end
- exception paths
- important business operations
- retries
- state transitions

Do not automatically label missing logs as defects.

Generate:

```text
logging_overview.md
logging_candidates.md
```

---

# 18. Test Analysis

Scan:

```text
JUnit
Mockito
TestNG
Spring Boot Test
integration tests
Spark tests
```

Build:

```text
ProductionMethod
     |
     v
TestMethod
```

Identify methods/classes with no obvious tests.

Generate:

```text
test_coverage_map.md
```

Do not claim actual coverage percentage unless a real coverage report is supplied.

---

# 19. Call Graph

The call graph is one of the highest-priority features.

Example:

```text
Controller.createUser
    |
    +--> Service.createUser
            |
            +--> Repository.findUser
            |
            +--> Validator.validate
            |
            +--> Repository.save
```

Support queries:

```text
Who calls X?
What does X call?
What is the full path from A to B?
What is impacted if X changes?
```

---

# 20. Impact Analysis

Given:

```text
UserService.createUser()
```

calculate:

```text
Direct callers
Indirect callers
Called methods
Interfaces
Implementations
Tests
SQL
Tables
Configuration
Endpoints
External services
```

Generate:

```text
impact_<symbol>.md
```

This will become one of the strongest capabilities for local coding.

---

# 21. Context Markdown Generation

This is a mandatory output.

Generate:

```text
.code-memory/
│
├── manifest.json
├── graph/
│   ├── nodes.json
│   ├── edges.json
│   └── graph_summary.json
│
├── context/
│   ├── 00_project_overview.md
│   ├── 01_architecture.md
│   ├── 02_modules.md
│   ├── 03_dependencies.md
│   ├── 04_configuration.md
│   ├── 05_database.md
│   ├── 06_api_endpoints.md
│   ├── 07_call_graph.md
│   ├── 08_data_flow.md
│   ├── 09_exception_flow.md
│   ├── 10_logging.md
│   ├── 11_tests.md
│   ├── 12_spark.md
│   ├── 13_sql.md
│   └── 14_ai_coding_instructions.md
│
├── symbols/
│   ├── classes/
│   ├── methods/
│   └── endpoints/
│
└── reports/
    ├── scan_report.md
    ├── unresolved_symbols.md
    ├── warnings.md
    └── quality_report.md
```

---

# 22. The Most Important Markdown File

Generate:

```text
14_ai_coding_instructions.md
```

This should explain to a local coding LLM:

```text
You are working with this repository.

Repository architecture:
...

Important modules:
...

Build system:
...

Java version:
...

Frameworks:
...

Database:
...

Known entrypoints:
...

Important conventions:
...

When modifying code:
1. inspect the target method
2. inspect callers
3. inspect callees
4. inspect tests
5. inspect configuration
6. inspect SQL/data flow
7. preserve existing architecture
8. avoid unrelated changes
9. update tests
10. explain file/line changes
```

This file is the "instruction layer" of Code Memory.

---

# 23. Task-Specific Context Generation

Do not force the LLM to read all Markdown files.

Provide:

```bash
code-memory context "Add retry handling to payment API"
```

The system should:

1. parse the task;
2. identify relevant symbols;
3. search vector DB;
4. query graph;
5. expand related nodes;
6. retrieve exact source;
7. retrieve tests;
8. retrieve config;
9. retrieve SQL if relevant;
10. generate a compact context pack.

Output:

```text
.code-memory/tasks/
    task_20260901_001/
        task.md
        relevant_files.md
        relevant_symbols.md
        call_graph.md
        data_flow.md
        tests.md
        configuration.md
        sql.md
        source_context.md
        llm_prompt.md
```

This is the preferred way to feed context to a local LLM.

---

# 24. Context Budgeting

The context generator must have a token budget.

Example configuration:

```yaml
context:
  max_tokens: 24000
  max_files: 30
  max_methods: 50
  max_graph_hops: 3
  max_vector_results: 30
  rerank_results: 10
```

Do not blindly generate enormous Markdown files.

The system should produce:

```text
global memory
+
task-specific memory
```

---

# 25. Confidence System

Every extracted relationship should have a confidence value.

Example:

```json
{
  "relationship": "CALLS",
  "source": "A.java:52",
  "target": "B.java:91",
  "confidence": 0.98
}
```

Use categories:

```text
HIGH
MEDIUM
LOW
UNKNOWN
```

The LLM prompt must instruct the model:

> Never treat inferred information as confirmed source truth.

---

# 26. Incremental Scanning

Do not rescan the entire repository every time.

Maintain file hashes.

```text
file_hash
scanner_version
last_scanned
```

If:

```text
UserService.java
```

changes:

1. detect hash change;
2. reparse file;
3. remove old entities from that file;
4. rebuild affected relationships;
5. update embeddings;
6. update Markdown;
7. update graph;
8. mark impacted nodes.

Support:

```bash
code-memory scan
code-memory scan --incremental
code-memory rebuild
code-memory clean
```

---

# 27. Versioned Code Memory

Every scan must have a version.

Example:

```text
memory_version:
2026-09-01T00:30:00Z

git_commit:
a83f2d1
```

Store:

```text
git commit
branch
scanner version
schema version
embedding model
LLM model
```

This is essential for debugging AI answers.

---

# 28. Git Integration

Read:

```text
.git
```

where available.

Capture:

```text
commit
branch
changed files
```

Support:

```bash
code-memory diff HEAD~1
```

Generate:

```text
change_impact.md
```

Later, use this for:

```text
"What changed in this commit?"
"What parts of the architecture are affected?"
```

---

# 29. Configuration

All behavior must be configuration-driven.

Create:

```text
config/
    application.yaml
    logging.yaml
```

Example:

```yaml
project:
  root: "./sample-project"

storage:
  metadata: "./data/metadata.db"

graph:
  provider: neo4j
  uri: bolt://localhost:7687
  database: neo4j

vector:
  provider: qdrant
  url: http://localhost:6333
  collection: code_memory

llm:
  provider: ollama
  model: qwen3-coder:30b
  temperature: 0.1

embedding:
  provider: local
  model: configurable

scanner:
  incremental: true
  include_tests: true
  include_resources: true
  max_file_size_mb: 10

context:
  max_tokens: 24000
  max_files: 30
  max_graph_hops: 3
```

Never hard-code:

- database URLs
- model names
- paths
- token limits
- logging levels
- scanner behavior

---

# 30. Logging Requirements

The project itself must have proper logging from the beginning.

Use structured logging.

At minimum:

```text
DEBUG
INFO
WARN
ERROR
```

Log:

```text
scan start/end
file discovered
file parsed
parse failure
symbol extracted
relationship created
graph update
embedding generated
vector update
context generation
LLM request
LLM response metadata
incremental scan changes
performance timings
```

Never log:

```text
password
API keys
tokens
secrets
private credentials
full sensitive configuration
```

Use correlation IDs:

```text
scan_id
request_id
task_id
```

Example:

```text
INFO scan_id=abc123 phase=AST_PARSE file=UserService.java duration_ms=42
```

---

# 31. Logging Configuration

Provide:

```yaml
logging:
  level: INFO
  console: true
  file:
    enabled: true
    path: "./logs/code-memory.log"
  structured: true
```

Allow package-specific levels:

```yaml
logging:
  levels:
    scanner: INFO
    graph: INFO
    vector: INFO
    retrieval: DEBUG
    llm: INFO
```

---

# 32. Comments and Documentation Requirements

The implementation generated by Claude must contain useful comments.

Do NOT comment every line.

Comments are required for:

- graph schema decisions
- parsing edge cases
- incremental update logic
- confidence calculation
- retrieval ranking
- token budgeting
- SQL extraction
- Spark flow reconstruction
- Spring mapping detection
- secret redaction
- retry logic
- external process invocation

Every major module must have a README or module-level documentation.

---

# 33. Error Handling

The scanner must be fault tolerant.

One broken Java file must not destroy the entire scan.

Use:

```text
ParseResult
    SUCCESS
    PARTIAL
    FAILED
```

Store failures in:

```text
unresolved_symbols.md
warnings.md
```

Continue scanning whenever possible.

---

# 34. Security / Privacy

The system is local-first.

Hard requirements:

- no OpenAI API
- no Anthropic API
- no cloud embedding API
- no cloud vector DB
- no cloud telemetry
- no source-code upload

Default behavior:

```text
Network access = disabled
```

except explicitly configured local services such as:

```text
localhost:11434
localhost:6333
localhost:7687
```

Add a privacy scanner that detects secrets before context generation.

---

# 35. CLI

Create a clean CLI.

Commands:

```bash
code-memory init
code-memory scan
code-memory scan --incremental
code-memory rebuild
code-memory stats
code-memory graph
code-memory search
code-memory impact
code-memory context
code-memory export
code-memory validate
code-memory doctor
```

Examples:

```bash
code-memory scan ./my-project

code-memory search "payment failure handling"

code-memory impact com.example.PaymentService.processPayment

code-memory context "Add retry handling to payment API"

code-memory export --format markdown
```

---

# 36. Local REST API

Add a local API.

Endpoints:

```text
GET  /health
POST /scan
GET  /project
GET  /stats
POST /search
POST /context
GET  /symbol/{id}
GET  /impact/{id}
GET  /callers/{id}
GET  /callees/{id}
GET  /graph/{id}
```

The API must never expose the service publicly by default.

Bind to:

```text
127.0.0.1
```

---

# 37. Graph Query API

Provide high-level queries instead of forcing callers to write Cypher.

Examples:

```python
find_callers(method_id)
find_callees(method_id)
find_implementations(interface_id)
find_impact(method_id)
find_endpoint_flow(endpoint_id)
find_database_usage(table)
find_config_usage(property)
find_tests_for_symbol(symbol)
```

This abstraction is critical for future backend replacement.

---

# 38. Hybrid Retrieval Algorithm

Implement:

```text
query
  |
  +--> lexical search
  |
  +--> vector search
  |
  +--> symbol lookup
  |
  +--> graph lookup
  |
  +--> call graph expansion
  |
  +--> dependency expansion
  |
  +--> source retrieval
  |
  v
Candidate set
  |
  v
Reranker
  |
  v
Evidence builder
  |
  v
Context pack
```

The context pack must prioritize:

1. exact target code;
2. direct callers;
3. direct callees;
4. tests;
5. configuration;
6. SQL/data flow;
7. related architecture;
8. broader semantic matches.

---

# 39. Local LLM Prompting

Create prompt templates:

```text
prompts/
    analyze_task.md
    explain_code.md
    find_fix.md
    impact_analysis.md
    add_logging.md
    refactor.md
    debug.md
    implement_feature.md
```

Every prompt should instruct the model to:

- cite files;
- cite line ranges where available;
- distinguish facts from inference;
- avoid inventing symbols;
- inspect relevant tests;
- explain assumptions;
- minimize unrelated modifications;
- prefer existing project patterns;
- propose verification steps.

---

# 40. LLM Output Contract

Prefer structured output.

Example:

```json
{
  "summary": "...",
  "confidence": "HIGH",
  "files_to_change": [
    {
      "file": "src/main/java/com/example/UserService.java",
      "lines": "82-126",
      "reason": "..."
    }
  ],
  "files_to_review": [],
  "tests_to_update": [],
  "risks": [],
  "implementation_plan": []
}
```

Then render this into Markdown for humans.

---

# 41. Code Editing Must Be Separate

Do not let V1 automatically modify production code.

V1 should produce:

```text
recommended changes
+
exact files
+
line ranges
+
patch suggestion
```

V2 can add:

```text
generate patch
```

V3 can add:

```text
apply patch
run tests
inspect failures
revise patch
```

This staged approach reduces risk.

---

# 42. Validation Framework

Create a sample Java repository specifically for testing the system.

It should contain:

```text
Spring Boot API
Service
Repository
SQL
Configuration
Exception handling
Logging
JUnit tests
```

Also create a Spark/ETL sample:

```text
Spark job
DataFrame transformations
SQL
input
join
aggregation
output
```

Known ground truth must be documented.

Example:

```text
UserController.createUser
 -> UserService.createUser
 -> UserRepository.save
```

The scanner must reproduce this relationship.

---

# 43. Automated Tests

Test every layer.

## Unit tests

```text
parser
symbol extraction
SQL parser
Spring analyzer
Spark analyzer
graph builder
embedding chunker
retrieval
ranking
context generator
secret redaction
configuration
```

## Integration tests

```text
Java repository
 -> scanner
 -> Neo4j
 -> Qdrant
 -> context
```

## End-to-end tests

Example:

```text
Question:
Where should I add logging for failed user creation?

Expected:
UserService.createUser
GlobalExceptionHandler
```

Use assertions on:

```text
file
symbol
line range
relationship
reason
```

---

# 44. Quality Metrics

The scanner needs measurable quality.

Track:

```text
file parse success rate
symbol resolution rate
call graph resolution rate
unresolved symbol count
graph node count
graph edge count
embedding count
retrieval precision
context size
```

Create:

```text
quality_report.md
```

Do not claim the system "understands everything".

Measure what it actually extracts.

---

# 45. Performance

Track timings for:

```text
file discovery
parsing
symbol extraction
graph construction
graph persistence
embedding
vector indexing
Markdown generation
retrieval
LLM
```

The scan report should show:

```text
Files scanned: 1,204
Java files: 832
Methods: 7,432
Classes: 1,024
Relationships: 38,421
SQL statements: 217
Endpoints: 83
Tests: 1,204
Embedding chunks: 9,812
Warnings: 17
Errors: 2
Duration: ...
```

---

# 46. Docker Support

Provide Docker Compose for infrastructure only:

```text
docker-compose.yml

Neo4j
Qdrant
```

Do not require the application itself to run inside Docker.

The local LLM can run through Ollama on the host.

Example:

```text
Host
 ├── code-memory
 ├── Ollama
 ├── Neo4j container
 └── Qdrant container
```

---

# 47. Directory Structure

Recommended repository:

```text
local-code-memory/
│
├── README.md
├── PLAN.md
├── pyproject.toml
├── Makefile
├── docker-compose.yml
│
├── config/
│
├── src/
│   └── code_memory/
│       ├── cli/
│       ├── api/
│       ├── scanner/
│       ├── parsers/
│       ├── analyzers/
│       │   ├── java/
│       │   ├── spring/
│       │   ├── spark/
│       │   ├── sql/
│       │   ├── logging/
│       │   ├── configuration/
│       │   └── dependencies/
│       ├── graph/
│       ├── vector/
│       ├── metadata/
│       ├── retrieval/
│       ├── context/
│       ├── embeddings/
│       ├── reranking/
│       ├── llm/
│       ├── security/
│       └── models/
│
├── prompts/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── sample_projects/
│   ├── spring_sql/
│   └── spark_etl/
│
├── scripts/
├── docs/
├── data/
├── logs/
└── .code-memory/
```

---

# 48. Implementation Phases

## Phase 0 — Architecture and infrastructure

Deliver:

- repository
- configuration
- logging
- CLI skeleton
- Docker Compose
- Neo4j
- Qdrant
- SQLite
- health checks
- tests

Do not implement LLM yet.

---

## Phase 1 — Repository scanner

Implement:

- file inventory
- Java detection
- Maven/Gradle detection
- resources
- SQL
- configuration
- tests

Output:

```text
project_inventory.json
```

---

## Phase 2 — Java semantic scanner

Implement:

- packages
- classes
- interfaces
- methods
- fields
- parameters
- annotations
- imports
- inheritance
- source locations

Output graph nodes.

---

## Phase 3 — Relationship extraction

Implement:

- CALLS
- EXTENDS
- IMPLEMENTS
- IMPORTS
- USES
- RETURNS
- THROWS
- CATCHES

Build first call graph.

---

## Phase 4 — Spring analyzer

Implement:

- controllers
- endpoints
- services
- repositories
- DI
- configuration
- exception handlers

Generate endpoint flow.

---

## Phase 5 — Spark analyzer

Implement:

- SparkSession
- DataFrame/Dataset
- transformations
- actions
- SQL
- inputs
- outputs
- job flow

---

## Phase 6 — SQL analyzer

Implement:

- SQL extraction
- parsing
- tables
- columns
- reads
- writes

---

## Phase 7 — Graph DB

Implement:

```text
GraphRepository
Neo4jGraphRepository
```

Persist normalized graph.

Add graph validation.

---

## Phase 8 — Vector DB

Implement:

- chunking
- local embedding
- Qdrant
- metadata filtering
- vector search

---

## Phase 9 — Hybrid retrieval

Implement:

```text
vector + lexical + graph + source
```

Add reranking.

---

## Phase 10 — Markdown memory generator

Generate:

```text
.code-memory/context/*
```

This phase must work without an LLM.

---

## Phase 11 — Task-specific context generator

Implement:

```bash
code-memory context "..."
```

Generate compact context pack.

---

## Phase 12 — Local LLM integration

Implement:

```text
OllamaProvider
```

Use configurable Qwen3-Coder.

The LLM should consume the task-specific context rather than the whole database.

---

## Phase 13 — Coding advisor

Implement:

```text
find_fix
impact_analysis
debug
add_logging
refactor
implement_feature
```

---

## Phase 14 — Git integration

Add:

- commit tracking
- changed files
- diff analysis
- change impact

---

## Phase 15 — Incremental memory

Implement:

```bash
scan --incremental
```

Only update affected files/nodes/embeddings/context.

---

## Phase 16 — Code patch generation

Only after retrieval accuracy is proven.

Generate:

```text
diff/patch
```

Do not auto-apply initially.

---

# 49. "Definition of Done" for V1

V1 is complete only if the system can scan a real Java backend and answer these WITHOUT a cloud service:

```text
1. What are the main modules?
2. What classes exist?
3. What methods exist?
4. Who calls this method?
5. What does this method call?
6. What is the endpoint -> service -> repository flow?
7. Which SQL does this method execute?
8. Which tables are read/written?
9. Which configuration properties affect this method?
10. Which tests cover this method?
11. What is affected if I change this method?
12. Where should logging be added?
13. Which files are relevant to a coding request?
14. Can the system generate a compact Markdown context pack?
15. Can a local coding LLM use that context to propose a change?
```

---

# 50. Claude Code Execution Rules

Claude should implement this project incrementally.

Do NOT attempt to generate the entire project in one response.

For each phase:

1. inspect current repository;
2. implement only the phase;
3. write tests;
4. run tests;
5. inspect failures;
6. fix failures;
7. update documentation;
8. update configuration examples;
9. update logging;
10. commit/checkpoint.

Never silently skip a failed test.

Do not replace a real implementation with a mock just to make tests pass.

---

# 51. Claude Coding Rules

Claude must:

- use type hints;
- use clear interfaces;
- keep modules small;
- avoid global state;
- use dependency injection;
- isolate external services;
- isolate graph DB implementation;
- isolate vector DB implementation;
- isolate LLM provider;
- isolate embedding provider;
- use structured logging;
- add meaningful comments;
- add tests;
- update documentation;
- preserve existing behavior;
- avoid hard-coded paths;
- avoid hard-coded model names;
- avoid secrets;
- avoid cloud APIs.

---

# 52. Missing/Optional Features to Decide Before Implementation

These are deliberately NOT mandatory for V1 unless the user chooses them.

## A. Full autonomous coding agent

```text
LLM
 -> inspect
 -> edit
 -> compile
 -> test
 -> diagnose
 -> edit again
```

Recommendation: **Do not include in V1.**

## B. Automatic code modification

Recommendation: **V2.**

## C. Code execution sandbox

Useful for running generated tests safely.

Recommendation: **V2/V3.**

## D. Multi-language support

Python/Scala/SQL beyond embedded SQL.

Recommendation: support SQL now; defer general multi-language support.

## E. IDE plugin

VS Code/IntelliJ integration.

Recommendation: defer until CLI/API is stable.

## F. Web UI

Graph visualization and search UI.

Recommendation: optional V2.

## G. Historical code memory

Store graph snapshots for every Git commit.

Recommendation: valuable, but after incremental scanning works.

## H. Architecture drift detection

Compare current architecture against previous scan.

Recommendation: V2.

## I. Security vulnerability analysis

Could integrate CodeQL/Semgrep later.

Recommendation: separate feature, not core V1.

---

# 53. Most Important Product Decision

The generated Markdown must NOT become a second, manually maintained source of truth.

It must always be generated from:

```text
Source Code
   +
Graph
   +
Vector Index
   +
Metadata
```

Therefore:

```text
.md files = generated cache/export
```

not:

```text
.md files = authoritative database
```

This prevents stale context.

---

# 54. Recommended Final Architecture

```text
                         JAVA PROJECT
                              |
                              v
                     ┌─────────────────┐
                     │ DEEP SCANNER    │
                     └────────┬────────┘
                              |
          ┌───────────────────┼───────────────────┐
          |                   |                   |
          v                   v                   v
       Java AST          Code Analysis        Build/Config
          |                   |                   |
          └───────────────────┼───────────────────┘
                              v
                    CODE KNOWLEDGE MODEL
                              |
              ┌───────────────┼────────────────┐
              |               |                |
              v               v                v
           Neo4j           Qdrant            SQLite
          Graph DB        Vector DB        Metadata
              |               |                |
              └───────────────┼────────────────┘
                              v
                    HYBRID RETRIEVAL ENGINE
                              |
                              v
                    TASK CONTEXT BUILDER
                              |
                 ┌────────────┴─────────────┐
                 |                          |
                 v                          v
          Markdown Context              Local LLM
                 |                          |
                 |                          v
                 |                    Coding Advisor
                 |                          |
                 └──────────────┬───────────┘
                                v
                         Human Developer
```

---

# 55. Success Criterion

The project should eventually allow this workflow:

```bash
code-memory scan ./my-java-project
```

then:

```bash
code-memory context "Add proper retry handling when the payment service fails"
```

and produce:

```text
task.md
relevant_files.md
relevant_symbols.md
call_graph.md
data_flow.md
configuration.md
sql.md
tests.md
source_context.md
llm_prompt.md
```

Then the local coding model receives those artifacts and can reason about the repository without sending source code to a paid AI service.

The system's strongest differentiator is therefore not the local LLM.

It is the **high-quality persistent Code Memory that tells the LLM what the repository actually contains and how its pieces are connected.**

---

# 56. Recommended Immediate Scope

For the first implementation, Claude should build ONLY:

```text
Java scanner
+
Spring/Spark detection
+
SQL extraction
+
Neo4j
+
Qdrant
+
SQLite
+
local embeddings
+
Markdown generator
+
hybrid retrieval
+
Ollama interface
+
Qwen3-Coder configurable model
+
CLI
+
logging
+
configuration
+
tests
```

Do not start with autonomous editing.

First prove:

```text
CODE
  -> MEMORY
  -> RETRIEVAL
  -> CONTEXT
  -> LOCAL LLM
```

Once that works reliably, add patch generation and autonomous coding.

---

# 57. Final Technical Principle

The system should follow this rule:

```text
Graph answers:
"What is connected to what?"

Vector search answers:
"What code is semantically relevant?"

Source retrieval answers:
"What does the actual code say?"

Metadata answers:
"When/how was this memory generated?"

LLM answers:
"Given this evidence, what should the developer do?"
```

That separation is the core architecture of the project.
