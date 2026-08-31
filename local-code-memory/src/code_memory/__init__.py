"""Local Code Memory & Local LLM Coding Assistant.

Phase 0 (infrastructure) and Phase 1 (repository scanner) are implemented.
See PLAN.md for the full roadmap.
"""

__version__ = "0.1.0"

# Version of the scanner logic. Bump whenever extraction output changes so that
# incremental scans know to reprocess files scanned by an older scanner.
SCANNER_VERSION = "1"

# Version of the graph schema (nodes/edges). Used by the metadata store.
SCHEMA_VERSION = "1"
