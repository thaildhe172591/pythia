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


def test_apply_token_is_content_bound():
    t1 = pythia.apply_token("PACKAGE BODY", "PKG_ORDER", "create...", "old src")
    assert len(t1) == 6 and all(c in "0123456789abcdef" for c in t1)
    assert pythia.apply_token("PACKAGE BODY", "PKG_ORDER", "create...", "old src") == t1
    # any ingredient changing invalidates the token
    assert pythia.apply_token("PACKAGE BODY", "PKG_ORDER", "create!!!", "old src") != t1
    assert pythia.apply_token("PACKAGE BODY", "PKG_ORDER", "create...", "NEW src") != t1
    assert pythia.apply_token("PACKAGE", "PKG_ORDER", "create...", "old src") != t1
    # CRLF normalization: a Windows checkout is not an edit
    assert pythia.apply_token("PACKAGE BODY", "PKG_ORDER", "create...\r\n", "old src") == \
           pythia.apply_token("PACKAGE BODY", "PKG_ORDER", "create...\n", "old src")


def test_effective_policy_defaults_and_overrides():
    eff = pythia.effective_policy(None)
    assert eff["plsql_source"] == ("confirm", "default")
    assert eff["data_dml"] == ("deny", "default")
    assert eff["session"] == ("allow", "default")
    eff = pythia.effective_policy({"data_dml": "confirm"})
    assert eff["data_dml"] == ("confirm", "policy.json")
    assert eff["plsql_source"] == ("confirm", "default")
    expect_exit(lambda: pythia.effective_policy({"data_dml": "yolo"}),
                "data_dml", "allow", "confirm", "deny")
    expect_exit(lambda: pythia.effective_policy({"typo_group": "deny"}), "typo_group")


def test_journal_entry_roundtrip():
    import datetime
    with tempfile.TemporaryDirectory() as td:
        now = datetime.datetime(2026, 8, 26, 14, 2, 11)
        eid = pythia.write_journal_entry(
            td, "PACKAGE BODY", "PKG_ORDER",
            before="PACKAGE BODY pkg_order AS\nold\nEND;",
            after="CREATE OR REPLACE PACKAGE BODY pkg_order AS\nnew\nEND;",
            meta={"connection": "DEV"}, now=now)
        assert eid == "2026-08-26T14-02-11_PKG_ORDER_PACKAGE-BODY"   # no colons: Windows-safe
        e = pythia.read_journal_entry(td, eid)
        assert e["before"].startswith("PACKAGE BODY")
        assert e["restore"].startswith("CREATE OR REPLACE PACKAGE BODY")
        assert e["meta"]["connection"] == "DEV" and e["meta"]["created"] is False
        # a new object: empty before, restore is a DROP, created is recorded
        eid2 = pythia.write_journal_entry(td, "PROCEDURE", "P_NEW", "",
                                          "CREATE OR REPLACE PROCEDURE p_new...",
                                          {}, now=now)
        e2 = pythia.read_journal_entry(td, eid2)
        assert e2["restore"].strip() == "DROP PROCEDURE P_NEW"
        assert e2["meta"]["created"] is True
        ids = pythia.list_journal_entries(td)
        assert eid in ids and eid2 in ids
        # same object, same second: the id is unique-ified, never overwritten
        eid3 = pythia.write_journal_entry(td, "PACKAGE BODY", "PKG_ORDER",
                                          "b", "a", {}, now=now)
        assert eid3 != eid and eid3.startswith(eid)


def test_newly_invalid_and_diff():
    before = [("A", "PROCEDURE"), ("B", "PACKAGE BODY")]
    after = [("A", "PROCEDURE"), ("B", "PACKAGE BODY"), ("C", "PROCEDURE")]
    assert pythia.newly_invalid(before, after) == [("C", "PROCEDURE")]
    assert pythia.newly_invalid(after, before) == []
    text, changed = pythia.render_diff("a\nb\nc\n", "a\nX\nc\n")
    assert "-b" in text and "+X" in text and changed == 2


class FakeCursor:
    def __init__(self, script, executed):
        self.script, self.executed = script, executed
        self.description, self._rows = None, []

    def execute(self, sql, binds=None):
        self.executed.append((sql, dict(binds or {})))
        low = " ".join(sql.lower().split())
        for key, value in self.script.items():
            if key in low:
                if isinstance(value, list):   # sequenced responses, e.g. the
                    cols, rows = value[0]     # INVALID set before vs after the
                    if len(value) > 1:        # write; the last response sticks
                        value.pop(0)
                else:
                    cols, rows = value
                self.description = [(c,) for c in cols]
                self._rows = rows
                return
        self.description, self._rows = None, []   # DDL/DML: no result set

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, script):
        self.script, self.executed = script, []

    def cursor(self):
        return FakeCursor(self.script, self.executed)


OLD_SRC = "PACKAGE BODY pkg_order AS\n  old line;\nEND;\n"
NEW_FILE = "CREATE OR REPLACE PACKAGE BODY pkg_order AS\n  new line;\nEND;\n/\n"


def base_script(errors=(), invalid_after=None, db_source=OLD_SRC):
    invalid_before = [("X_ALREADY_BROKEN", "PROCEDURE")]
    return {
        "from all_source": ([("TEXT",)], [(ln + "\n",) for ln in db_source.splitlines()]),
        "from all_dependencies": ([("LVL", "OWNER", "NAME", "TYPE", "STATUS",
                                    "DEPENDENCY_TYPE")],
                                  [(1, "APP", "P_USES_ORDER", "PROCEDURE",
                                    "VALID", "HARD")]),
        "status = 'invalid'": (
            [(("OBJECT_NAME", "OBJECT_TYPE", "LAST_DDL"),
              [(n, t, "2026-01-01") for n, t in invalid_before])]
            + ([(("OBJECT_NAME", "OBJECT_TYPE", "LAST_DDL"),
                 [(n, t, "2026-01-01") for n, t in invalid_after])]
               if invalid_after is not None else [])),
        "from all_errors": ([("NAME", "TYPE", "SEQUENCE", "LINE", "POSITION",
                              "ATTRIBUTE", "TEXT")], list(errors)),
        "from session_privs": ([("PRIVILEGE",)], []),
    }


def apply_ns(root, **kw):
    import argparse
    ns = argparse.Namespace(file=None, confirm=None, yes=False, json=False,
                            depth=3, limit=200, max_lines=2000, offset=0,
                            raw=False, command="apply", conn_name="DEV",
                            conn_user="app", schema="APP", project_root=root)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def wrote_ddl(conn):
    return [s for s, _ in conn.executed if s.lstrip().lower().startswith("create")]


def test_apply_preview_writes_nothing_and_gives_token():
    with tempfile.TemporaryDirectory() as td:
        conn = FakeConn(base_script())
        code = pythia.run_apply(conn, "APP", apply_ns(td), NEW_FILE)
        assert code == 0
        assert wrote_ddl(conn) == []                      # preview never writes
        ids = pythia.list_journal_entries(td)
        assert len(ids) == 1                              # snapshot persisted
        assert pythia.read_journal_entry(td, ids[0])["meta"]["applied"] is False


def test_apply_correct_token_writes_and_verifies_clean():
    with tempfile.TemporaryDirectory() as td:
        tok = pythia.apply_token("PACKAGE BODY", "PKG_ORDER", NEW_FILE, OLD_SRC)
        conn = FakeConn(base_script())
        code = pythia.run_apply(conn, "APP", apply_ns(td, confirm=tok), NEW_FILE)
        assert code == 0
        assert len(wrote_ddl(conn)) == 1
        # snapshot precedes the write: entry exists with the pre-write source
        eid = pythia.list_journal_entries(td)[0]
        e = pythia.read_journal_entry(td, eid)
        assert e["before"].rstrip() == OLD_SRC.rstrip()
        assert e["meta"]["applied"] is True


def test_apply_stale_token_refused():
    with tempfile.TemporaryDirectory() as td:
        tok = pythia.apply_token("PACKAGE BODY", "PKG_ORDER", "something else", OLD_SRC)
        conn = FakeConn(base_script())
        expect_exit(lambda: pythia.run_apply(conn, "APP", apply_ns(td, confirm=tok),
                                             NEW_FILE),
                    "changed", "preview")
        assert wrote_ddl(conn) == []


def test_apply_policy_deny_refuses_without_touching_db():
    with tempfile.TemporaryDirectory() as td:
        conn = FakeConn(base_script())
        expect_exit(lambda: pythia.run_apply(conn, "APP", apply_ns(td),
                                             "delete from t_order"),
                    "data_dml", "deny", "no snapshot")
        assert conn.executed == []                        # not even a read
        expect_exit(lambda: pythia.run_apply(conn, "APP", apply_ns(td),
                                             "BEGIN evil; END;"), "anonymous")
        assert conn.executed == []


def test_apply_broken_compile_exits_3_with_restore_hint():
    errors = [("PKG_ORDER", "PACKAGE BODY", 1, 47, 12, "ERROR",
               "PLS-00201: identifier 'CALC_TAX' must be declared")]
    invalid_after = [("X_ALREADY_BROKEN", "PROCEDURE"),
                     ("P_USES_ORDER", "PROCEDURE")]
    with tempfile.TemporaryDirectory() as td:
        tok = pythia.apply_token("PACKAGE BODY", "PKG_ORDER", NEW_FILE, OLD_SRC)
        conn = FakeConn(base_script(errors=errors, invalid_after=invalid_after))
        code = pythia.run_apply(conn, "APP", apply_ns(td, confirm=tok), NEW_FILE)
        assert code == 3                                  # applied but broken
        eid = pythia.list_journal_entries(td)[0]
        meta = pythia.read_journal_entry(td, eid)["meta"]
        assert meta["applied"] is True
        assert ["P_USES_ORDER", "PROCEDURE"] in [list(x) for x in meta["newly_invalid"]]


def test_apply_yes_previews_and_writes_in_one_run():
    with tempfile.TemporaryDirectory() as td:
        conn = FakeConn(base_script())
        code = pythia.run_apply(conn, "APP", apply_ns(td, yes=True), NEW_FILE)
        assert code == 0 and len(wrote_ddl(conn)) == 1


def test_apply_snapshot_survives_failed_execute():
    class Exploding(FakeConn):
        def cursor(self):
            outer = self

            class C(FakeCursor):
                def execute(self, sql, binds=None):
                    if sql.lstrip().lower().startswith("create"):
                        outer.executed.append((sql, {}))
                        raise RuntimeError("ORA-00600 simulated")
                    return super().execute(sql, binds)
            return C(self.script, self.executed)

    with tempfile.TemporaryDirectory() as td:
        tok = pythia.apply_token("PACKAGE BODY", "PKG_ORDER", NEW_FILE, OLD_SRC)
        conn = Exploding(base_script())
        try:
            pythia.run_apply(conn, "APP", apply_ns(td, confirm=tok), NEW_FILE)
        except RuntimeError:
            pass
        ids = pythia.list_journal_entries(td)
        assert len(ids) == 1                              # snapshot was already on disk
        assert pythia.read_journal_entry(td, ids[0])["before"].rstrip() == OLD_SRC.rstrip()


def test_apply_schema_mismatch_refused():
    with tempfile.TemporaryDirectory() as td:
        conn = FakeConn(base_script())
        expect_exit(lambda: pythia.run_apply(
            conn, "APP", apply_ns(td),
            "CREATE OR REPLACE PROCEDURE other_schema.p AS BEGIN NULL; END;"),
            "OTHER_SCHEMA", "APP")
        assert conn.executed == []


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
