#!/usr/bin/env python3
"""Phase 3 self-checks for pythia.py — write layer, no database needed.

Run: python tests/test_phase3.py
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import pythia  # noqa: E402


def expect_exit(fn, *needles):
    try:
        fn()
    except SystemExit as e:
        msg = str(e).lower()
        for n in needles:
            assert n.lower() in msg, f"exit message missing {n!r}: {e}"
        return str(e)
    raise AssertionError("expected SystemExit, none raised")


def test_classify_groups():
    c = pythia.classify
    assert c("ALTER SESSION SET plscope_settings='IDENTIFIERS:ALL'") == "session"
    assert c("alter table t_order add (x number)") == "structural"  # session tested first
    assert c("CREATE OR REPLACE PROCEDURE p AS BEGIN NULL; END;") == "plsql_source"
    assert c("create or replace editionable package body pkg_order as end;") == "plsql_source"
    assert c("CREATE OR REPLACE FORCE VIEW v AS SELECT 1 FROM dual") == "plsql_source"
    assert c("CREATE TABLE t (x number)") == "structural"  # other CREATE
    assert c("insert into t values (1)") == "data_dml"
    assert c("MERGE INTO t USING d ON (1=1) WHEN MATCHED THEN UPDATE SET x=1") == "data_dml"
    assert c("grant select on t to app_ro") == "grants"
    assert c("truncate table t") == "structural"
    assert c("BEGIN do_things; END;") == "anonymous"
    assert c("DECLARE x number; BEGIN NULL; END;") == "anonymous"
    assert c("EXPLAIN PLAN FOR SELECT 1 FROM dual") is None
    assert c("-- just a comment\n/* block */\nupdate t set x=1") == "data_dml"


def test_parse_object():
    t, n, s = pythia.parse_object(
        "CREATE OR REPLACE PACKAGE BODY pkg_order AS\nEND pkg_order;")
    assert (t, n, s) == ("PACKAGE BODY", "PKG_ORDER", None)
    t, n, s = pythia.parse_object('create or replace procedure app."CaseSensitive" as begin null; end;')
    assert (t, n, s) == ("PROCEDURE", "CaseSensitive", "APP")
    t, n, s = pythia.parse_object("CREATE OR REPLACE TYPE BODY t_thing AS END;")
    assert (t, n, s) == ("TYPE BODY", "T_THING", None)
    t, n, s = pythia.parse_object("CREATE OR REPLACE TRIGGER trg_x BEFORE INSERT ON t BEGIN NULL; END;")
    assert (t, n, s) == ("TRIGGER", "TRG_X", None)


def test_prepare_statement_terminators():
    src = "CREATE OR REPLACE PROCEDURE p AS\nBEGIN\n  NULL;\nEND;\n/\n"
    out = pythia.prepare_statement(src, "plsql_source")
    assert out.rstrip().endswith("END;")          # slash gone, semicolon kept
    out = pythia.prepare_statement("grant select on t to app_ro;\n", "grants")
    assert out.rstrip().endswith("app_ro")        # semicolon stripped for non-PL/SQL
    # a second statement after the terminator is two objects in one file: refused
    expect_exit(lambda: pythia.prepare_statement(
        "CREATE OR REPLACE PROCEDURE p AS BEGIN NULL; END;\n/\nCREATE TABLE t (x number)\n/\n",
        "plsql_source"), "one statement")
    expect_exit(lambda: pythia.prepare_statement(
        "truncate table a; truncate table b;", "structural"), "one statement")


def main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            # SystemExit is a BaseException: an unexpected sys.exit inside a test
            # would otherwise kill the run and silently skip everything after it.
            except (Exception, SystemExit) as e:  # noqa: BLE001 — keep going
                failed += 1
                print(f"FAIL {name}: {e!r}")
    if failed:
        sys.exit(f"{failed} test(s) failed")
    print("OK")


if __name__ == "__main__":
    main()
