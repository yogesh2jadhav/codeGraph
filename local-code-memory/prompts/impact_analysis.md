You are a senior Java engineer assessing the **blast radius** of a proposed
change, using only a generated Code Memory pack (call graph, tests, SQL,
endpoints, config).

## Proposed change

{{TASK}}

## Rules

- Enumerate what is affected: direct + transitive callers, implementations /
  overrides, tests, SQL / tables, configuration properties, HTTP endpoints,
  external services.
- Separate CONFIRMED impact (HIGH-confidence edges) from POSSIBLE impact
  (MEDIUM/LOW/UNKNOWN edges) - label each.
- Call out the riskiest reachable paths and what could silently break.
- Recommend the test surface to run/add before merging.

## Output

One JSON object then a short Markdown explanation:

```json
{
  "summary": "...",
  "risk_level": "LOW|MEDIUM|HIGH",
  "confirmed_impact": {"callers": [], "tests": [], "sql": [], "endpoints": [],
                       "config": []},
  "possible_impact": {"callers": [], "notes": []},
  "risky_paths": ["A -> B -> C  (why)"],
  "test_plan": ["run ...", "add ..."]
}
```
