"""cmd_context must print the *whole* parsed advice to the console, not just
`files_to_change` - explain_code (and other non-implement_feature modes) use a
different JSON schema (`walkthrough`, `calls_out_to`, ...) and previously
printed almost nothing beyond the one-line summary, even though advice.md had
the full answer. A user hit exactly this and concluded the feature "didn't
work" when it had actually written a complete walkthrough to disk.
"""

from code_memory.cli.main import _print_advice_detail


def test_prints_files_to_change_like_before(capsys):
    _print_advice_detail({
        "summary": "x", "confidence": "HIGH",
        "files_to_change": [{"file": "A.java", "lines": "1-2", "reason": "r"}],
    })
    out = capsys.readouterr().out
    assert "files to change:" in out
    assert "A.java" in out and "1-2" in out


def test_prints_explain_code_walkthrough_not_just_summary(capsys):
    _print_advice_detail({
        "summary": "s", "confidence": "HIGH",
        "walkthrough": [
            {"lines": "14-18", "explanation": "orchestrates the migration"},
            {"lines": "20", "explanation": "extracts rows"},
        ],
        "calls_out_to": [{"target": "DriverManager.getConnection",
                          "why": "opens a DB connection"}],
        "data_touched": [{"kind": "table", "name": "migrated_rows", "how": "writes"}],
        "returns": "void",
        "error_handling": ["throws on DB failure"],
    })
    out = capsys.readouterr().out
    assert "walkthrough:" in out
    assert "lines 14-18" in out and "orchestrates the migration" in out
    assert "calls out to:" in out and "DriverManager.getConnection" in out
    assert "data touched:" in out and "migrated_rows" in out
    assert "returns: void" in out
    assert "error handling:" in out and "throws on DB failure" in out


def test_skips_empty_and_meta_keys(capsys):
    _print_advice_detail({"summary": "s", "confidence": "HIGH",
                          "risk_level": "LOW", "root_cause": "x",
                          "risks": []})
    out = capsys.readouterr().out
    assert out == ""


def test_truncates_long_lists(capsys):
    _print_advice_detail({"risks": [f"risk {i}" for i in range(20)]}, max_items=3)
    out = capsys.readouterr().out
    assert out.count("- risk ") == 3
    assert "+17 more" in out
