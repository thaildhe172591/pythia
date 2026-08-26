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


def test_rank_similar_orders_by_shared_name_tokens():
    cands = [("PKG_ORDER_TOTAL_IN", "PROCEDURE", "VALID", "2026-01-01"),
             ("PKG_ORDER_TOTAL_LIST_CT", "PROCEDURE", "VALID", "2026-01-02"),
             ("HT_CODE_MAP", "PROCEDURE", "VALID", "2026-01-03"),
             ("PKG_ORDER_TOTAL_LIST", "PROCEDURE", "VALID", "2026-01-04")]
    out = pythia.rank_similar("PKG_ORDER_TOTAL_LIST", cands)
    assert [r[0] for r in out] == ["PKG_ORDER_TOTAL_LIST_CT", "PKG_ORDER_TOTAL_IN"]
    # self-match dropped, zero-overlap dropped, most shared tokens first
    assert out[0][-1] == "LIST ORDER PKG TOTAL"     # why it matched, sorted
    assert out[0][1] == "PROCEDURE"                 # original columns preserved


def test_rank_similar_is_case_insensitive_and_breaks_ties_by_name():
    out = pythia.rank_similar("pkg_order_calc",
                              [("PKG_ORDER_TOTAL", "PACKAGE", "VALID", "2026-01-01"),
                               ("PKG_ORDER_ITEM", "PACKAGE", "VALID", "2026-01-01")])
    assert [r[0] for r in out] == ["PKG_ORDER_ITEM", "PKG_ORDER_TOTAL"]
    assert pythia.rank_similar("ANYTHING", []) == []


def test_per_command_limit_default_does_not_leak():
    """argparse shares parent actions between subparsers, so a per-command
    default set on one can silently change every other command's default."""
    p = pythia.build_parser()
    assert p.parse_args(["similar", "X"]).limit == 20   # short list of examples
    assert p.parse_args(["ls", "X"]).limit == 200       # unchanged by similar
    assert p.parse_args(["grep", "X"]).limit == 200


def test_plscope_message_distinguishes_disabled_from_missing():
    disabled = pythia.plscope_message("CALC_TAX", has_any_data=False)
    assert "plscope_settings" in disabled       # how to turn it on
    assert "shared" in disabled.lower()         # warns before recompiling
    assert "grep" in disabled                   # what to use meanwhile
    missing = pythia.plscope_message("CALC_TAX", has_any_data=True)
    assert "plscope_settings" not in missing    # already on — do not misdirect
    assert "CALC_TAX" in missing


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
