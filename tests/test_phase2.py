#!/usr/bin/env python3
"""Phase 2 self-checks for pythia.py — pure logic only, no database needed.

Run: python tests/test_phase2.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import pythia  # noqa: E402


def test_query_binds_ignores_comments_and_literals():
    sql = """-- Purpose: demo, mentions :notabind in a comment
             select to_char(d, 'hh24:mi:ss') txt
               from t where owner = :s and name = upper(:n)"""
    assert pythia.query_binds(sql) == {"s", "n"}


def test_query_bind_contract_matches_files():
    on_disk = {p.name for p in pythia.QUERY_DIR.glob("*.sql")}
    assert on_disk == set(pythia.QUERY_BINDS), (
        f"queries/ and QUERY_BINDS drifted: "
        f"only on disk {on_disk - set(pythia.QUERY_BINDS)}, "
        f"only declared {set(pythia.QUERY_BINDS) - on_disk}")
    for name, declared in pythia.QUERY_BINDS.items():
        actual = pythia.query_binds(pythia.load_query(name))
        assert actual == declared, f"{name}: file uses {actual}, code declares {declared}"


def test_every_query_has_a_header_comment():
    for name in pythia.QUERY_BINDS:
        text = pythia.load_query(name)
        for field in ("-- Purpose:", "-- Binds:", "-- Returns:"):
            assert field in text, f"{name} is missing a '{field}' header line"


def test_format_errors_groups_by_object():
    rows = [("PKG_ORDER", "PACKAGE BODY", 1, 12, 3, "ERROR",
             "PLS-00201: identifier 'CALC_TAX' must be declared\n"),
            ("PKG_ORDER", "PACKAGE BODY", 2, 40, 1, "ERROR",
             "PL/SQL: Statement ignored"),
            ("P_SETTLE", "PROCEDURE", 1, 5, 9, "WARNING",
             "PLW-06002: Unreachable code")]
    out = pythia.format_errors(rows)
    assert out.count("PKG_ORDER (PACKAGE BODY)") == 1   # one header per object
    assert "  12:3 ERROR PLS-00201" in out              # line:col Oracle reported
    assert "  40:1 ERROR PL/SQL: Statement ignored" in out
    assert "P_SETTLE (PROCEDURE)" in out
    assert pythia.format_errors([]) == ""


def test_render_tree_indents_by_level():
    rows = [(1, "APP", "T_ORDER", "TABLE", "HARD"),
            (1, "APP", "PKG_TAX", "PACKAGE", "HARD"),
            (2, "APP", "T_RATE", "TABLE", "HARD")]
    lines = pythia.render_tree(rows, "APP.PKG_ORDER").splitlines()
    assert lines[0] == "APP.PKG_ORDER"
    assert lines[1] == "  APP.T_ORDER (TABLE)"
    assert lines[2] == "  APP.PKG_TAX (PACKAGE)"
    assert lines[3] == "    APP.T_RATE (TABLE)"
    # extra columns are ignored, so impact rows render with the same function
    wide = [(1, "APP", "P_SETTLE", "PROCEDURE", "VALID", "HARD")]
    assert pythia.render_tree(wide, "APP.T_ORDER").splitlines()[1] == \
        "  APP.P_SETTLE (PROCEDURE)"


def test_impact_summary_counts_unique_objects():
    rows = [(1, "APP", "P_SETTLE", "PROCEDURE", "VALID", "HARD"),
            (2, "APP", "P_REPORT", "PROCEDURE", "INVALID", "HARD"),
            (2, "APP", "P_SETTLE", "PROCEDURE", "VALID", "HARD")]  # second path
    assert pythia.impact_summary(rows) == \
        "-- impact: 2 dependent objects, 1 currently VALID"
    assert pythia.impact_summary([]) == \
        "-- impact: 0 dependent objects, 0 currently VALID"


def main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001 — report every failure, keep going
                failed += 1
                print(f"FAIL {name}: {e}")
    if failed:
        sys.exit(f"{failed} test(s) failed")
    print("OK")


if __name__ == "__main__":
    main()
