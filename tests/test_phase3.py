#!/usr/bin/env python3
"""Phase 3 self-checks for pythia.py — write layer, no database needed.

Run: python tests/test_phase3.py
"""
import json
import pathlib
import sys
import tempfile

import os
os.environ.setdefault("PYTHIA_CI", "1")   # the suites are headless by design

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


def test_mask_literals_keeps_length_and_blanks_text():
    m = pythia.mask_literals
    src = "update t set n = 'x where y' where id = 1"
    assert len(m(src)) == len(src)
    assert "where y" not in m(src)
    assert m("delete from t -- where later" + chr(10)
             + "where id = 1").count("where") == 1
    assert m("delete from t /* where */ where id = 1").count("where") == 1
    assert "it" not in m("update t set n = 'it''s' where id = 1")


def test_dml_probe_reads_target_and_predicate():
    assert pythia.dml_probe("delete from t_order where status = 'DRAFT'") == (
        "delete", "t_order", "status = 'DRAFT'")
    assert pythia.dml_probe("delete app.t_order t where t.id = 7") == (
        "delete", "app.t_order t", "t.id = 7")
    assert pythia.dml_probe("update t_order t set status = 'X' "
                            "where t.id = 7") == (
        "update", "t_order t", "t.id = 7")
    assert pythia.dml_probe("delete from t_order") == ("delete", "t_order", None)
    assert pythia.dml_probe("insert into t values (1)") is None


def test_dml_probe_refuses_what_it_cannot_measure():
    expect_exit(lambda: pythia.dml_probe(
        "merge into t using d on (1=1) when matched then update set x=1"),
        "merge", "join")
    expect_exit(lambda: pythia.dml_probe(
        "delete from t where id in (select id from u)"), "subquery")
    expect_exit(lambda: pythia.dml_probe(
        "update t set a = 1 where id in (1) and x = 2 where y = 3"),
        "more than one where")
    expect_exit(lambda: pythia.dml_probe(
        "update t set n = q'{x}' where id = 1"), "quoted")
    expect_exit(lambda: pythia.dml_probe("update t where id = 1"), "set clause")


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
    def __init__(self, script, executed, rowcount=0):
        self.script, self.executed = script, executed
        self.description, self._rows = None, []
        self.rowcount = rowcount

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
    def __init__(self, script, rowcount=0):
        self.script, self.executed = script, []
        self.rowcount = rowcount
        self.commits, self.rollbacks = 0, 0

    def cursor(self):
        return FakeCursor(self.script, self.executed, self.rowcount)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


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
        # main-namespace occupants of the target name (type-conflict check)
        "object_name = upper(:n)": ([("OBJECT_TYPE",)], [("PACKAGE BODY",)]),
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


DML_FILE = "delete from t_order where status = 'DRAFT'\n"


def dml_script(count=2, hashes=(11, 1, 9), rows=((7, "DRAFT"), (8, "DRAFT"))):
    s = base_script()
    s["ora_hash"] = (("N", "S", "LO", "HI"), [(count,) + tuple(hashes)])
    s["select * from"] = (("ID", "STATUS"), list(rows))
    return s


def dml_ns(root, **kw):
    ns = apply_ns(root, **kw)
    ns.file = "d.sql"
    return ns


def allow_dml(root):
    d = pathlib.Path(root) / ".pythia"
    d.mkdir(exist_ok=True)
    (d / "policy.json").write_text(json.dumps({"data_dml": "confirm"}),
                                   encoding="utf-8")


def wrote_dml(conn):
    return [s for s, _ in conn.executed
            if s.lstrip().lower().startswith(("delete", "update", "insert"))]


def test_dml_preview_measures_the_row_set_and_writes_nothing():
    with tempfile.TemporaryDirectory() as td:
        allow_dml(td)
        conn = FakeConn(dml_script())
        code = pythia.run_apply(conn, "APP", dml_ns(td), DML_FILE)
        assert code == 0
        assert wrote_dml(conn) == [] and conn.commits == 0
        entry = pythia.list_journal_entries(td)[0]
        rs = pythia.read_journal_entry(td, entry)["meta"]["row_set"]
        assert rs["count"] == 2 and rs["target"] == "t_order"
        assert rs["predicate"] == "status = 'DRAFT'"
        assert rs["sample"] == [["7", "DRAFT"], ["8", "DRAFT"]]


def test_the_row_set_moves_the_token():
    with tempfile.TemporaryDirectory() as td:
        allow_dml(td)
        pythia.run_apply(FakeConn(dml_script(count=2)), "APP",
                         dml_ns(td), DML_FILE)
        first = pythia.read_journal_entry(
            td, pythia.list_journal_entries(td)[0])["meta"]["token"]
        pythia.run_apply(FakeConn(dml_script(count=3)), "APP",
                         dml_ns(td), DML_FILE)
        second = pythia.read_journal_entry(
            td, pythia.list_journal_entries(td)[0])["meta"]["token"]
        assert first != second, "the fingerprint must reach the token"


def approved(td, conn):
    """Preview, then approve — the two-step gate, in test form. Returns the
    token the agent would pass to --confirm."""
    pythia.run_apply(conn, "APP", dml_ns(td), DML_FILE)
    entry = pythia.list_journal_entries(td)[0]
    token = pythia.read_journal_entry(td, entry)["meta"]["token"]
    pythia.mint_grant(td, token, "DEV")
    return token


def test_dml_commits_once_when_the_rowcount_matches():
    with tempfile.TemporaryDirectory() as td:
        allow_dml(td)
        token = approved(td, FakeConn(dml_script(count=2)))
        conn = FakeConn(dml_script(count=2), rowcount=2)
        code = pythia.run_apply(conn, "APP", dml_ns(td, confirm=token), DML_FILE)
        assert code == 0
        assert conn.commits == 1 and conn.rollbacks == 0
        assert wrote_dml(conn)


def test_dml_rolls_back_when_the_rowcount_diverges():
    with tempfile.TemporaryDirectory() as td:
        allow_dml(td)
        token = approved(td, FakeConn(dml_script(count=2)))
        conn = FakeConn(dml_script(count=2), rowcount=5)
        expect_exit(lambda: pythia.run_apply(
            conn, "APP", dml_ns(td, confirm=token), DML_FILE),
            "rolled back", "5", "2")
        assert conn.rollbacks == 1 and conn.commits == 0


def test_a_moved_row_set_refuses_the_confirm():
    with tempfile.TemporaryDirectory() as td:
        allow_dml(td)
        token = approved(td, FakeConn(dml_script(count=2)))
        conn = FakeConn(dml_script(count=3), rowcount=3)
        expect_exit(lambda: pythia.run_apply(
            conn, "APP", dml_ns(td, confirm=token), DML_FILE),
            "row set moved", "2 rows", "3")
        assert conn.commits == 0 and wrote_dml(conn) == []


def test_dml_preview_promises_no_object_and_no_rollback_file():
    """A DML statement is not a "new object", and the restore.sql generated
    for it is a DROP nothing would accept — the preview must offer neither."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        allow_dml(td)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            pythia.run_apply(FakeConn(dml_script()), "APP", dml_ns(td), DML_FILE)
        text = out.getvalue()
        assert "data_dml statement on APP" in text
        assert "new object" not in text and "restore.sql" not in text


def test_restore_refuses_an_entry_that_has_no_undo():
    with tempfile.TemporaryDirectory() as td:
        entry = pythia.write_journal_entry(
            td, "DATA_DML", "STATEMENT", "",
            "delete from t_order where id = 7",
            {"connection": "DEV", "group": "data_dml", "applied": True})
        ns = dml_ns(td, id=entry)
        expect_exit(lambda: pythia.run_restore(FakeConn(base_script()),
                                               "APP", ns),
                    "no undo", "flashback")


def test_deny_message_explains_what_revalidation_buys():
    with tempfile.TemporaryDirectory() as td:
        expect_exit(lambda: pythia.run_apply(FakeConn(dml_script()), "APP",
                                             dml_ns(td), DML_FILE),
                    "deny", "revalidation", "policy set data_dml confirm")


def test_approve_records_the_row_set_it_showed():
    with tempfile.TemporaryDirectory() as td:
        allow_dml(td)
        pythia.run_apply(FakeConn(dml_script(count=2)), "APP",
                         dml_ns(td), DML_FILE)
        meta = pythia.read_journal_entry(
            td, pythia.list_journal_entries(td)[0])["meta"]
        rec = pythia.mint_grant(
            td, meta["token"], "DEV",
            revalidate=pythia.fingerprint_text(meta["row_set"]))
        assert rec["revalidate"].startswith("rows=2 hash=")
        assert pythia.read_grant(td, meta["token"])["revalidate"] \
            == rec["revalidate"]


def test_insert_needs_no_row_set():
    with tempfile.TemporaryDirectory() as td:
        allow_dml(td)
        conn = FakeConn(base_script())
        code = pythia.run_apply(conn, "APP", dml_ns(td),
                                "insert into t_order (id) values (1)\n")
        assert code == 0
        assert not [s for s, _ in conn.executed if "ora_hash" in s.lower()]
        entry = pythia.list_journal_entries(td)[0]
        assert pythia.read_journal_entry(td, entry)["meta"]["row_set"] is None


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
        pythia.mint_grant(td, tok, "DEV")     # the developer approved it
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
        pythia.mint_grant(td, tok, "DEV")     # the developer approved it
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
        pythia.mint_grant(td, tok, "DEV")     # the developer approved it
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


def test_journal_restore_runs_the_six_steps():
    with tempfile.TemporaryDirectory() as td:
        eid = pythia.write_journal_entry(td, "PACKAGE BODY", "PKG_ORDER",
                                         OLD_SRC, NEW_FILE, {"connection": "DEV"})
        conn = FakeConn(base_script(db_source="PACKAGE BODY pkg_order AS\n  new line;\nEND;\n"))
        ns = apply_ns(td, yes=True)
        ns.command, ns.action, ns.id, ns.file = "journal", "restore", eid, f"journal:{eid}"
        code = pythia.run_restore(conn, "APP", ns)
        assert code == 0
        assert len(wrote_ddl(conn)) == 1
        assert wrote_ddl(conn)[0].lstrip().upper().startswith("CREATE OR REPLACE PACKAGE BODY")
        # the restore created its own new journal entry pointing back
        ids = pythia.list_journal_entries(td)
        assert len(ids) == 2
        newest = [i for i in ids if i != eid][0]
        assert pythia.read_journal_entry(td, newest)["meta"]["restored_from"] == eid


def test_journal_restore_of_created_object_is_a_drop_and_policy_gates_it():
    with tempfile.TemporaryDirectory() as td:
        eid = pythia.write_journal_entry(td, "PROCEDURE", "P_NEW", "",
                                         "CREATE OR REPLACE PROCEDURE p_new AS BEGIN NULL; END;",
                                         {})
        conn = FakeConn(base_script())
        ns = apply_ns(td, yes=True)
        ns.action, ns.id, ns.file = "restore", eid, f"journal:{eid}"
        # restore.sql is DROP PROCEDURE — structural, deny by default, honest refusal
        expect_exit(lambda: pythia.run_restore(conn, "APP", ns),
                    "structural", "deny")
        assert wrote_ddl(conn) == []


def test_docstring_no_longer_claims_readonly_build():
    doc = pythia.__doc__
    assert "READ-ONLY" not in doc
    assert "pythia apply" in doc and "pythia policy" in doc
    # the sql command's SELECT/WITH-only gate is still documented
    assert "SELECT/WITH" in doc


def test_write_path_skips_readonly_transaction():
    """Read commands keep SET TRANSACTION READ ONLY as defence 2. The write
    path must not get it: DML under a read-only transaction dies with
    ORA-01456; its defences are the classifier, policy, token and snapshot."""
    assert pythia.session_should_be_readonly("check")
    assert pythia.session_should_be_readonly("sql")
    assert pythia.session_should_be_readonly("journal", "list")
    assert not pythia.session_should_be_readonly("apply")
    assert not pythia.session_should_be_readonly("journal", "restore")


def test_name_conflicts_namespace_rules():
    nc = pythia.name_conflicts
    assert nc("FUNCTION", ["PROCEDURE"]) == ["PROCEDURE"]     # ORA-00955 waiting
    assert nc("PROCEDURE", ["PROCEDURE"]) == []               # same type: replace
    assert nc("PACKAGE BODY", ["PACKAGE", "PACKAGE BODY"]) == []
    assert nc("TYPE BODY", ["TYPE"]) == []
    assert nc("TRIGGER", ["PROCEDURE"]) == []   # triggers live in their own namespace
    assert nc("PROCEDURE", ["TABLE"]) == ["TABLE"]            # tables share the namespace


def test_apply_refuses_changing_an_objects_type():
    """Found in the field: a FUNCTION file over an existing PROCEDURE previewed
    as 'new object' and only failed at apply time with ORA-00955. The preview
    must refuse instead of promising what the database will reject."""
    script = base_script()
    script["object_name = upper(:n)"] = ([("OBJECT_TYPE",)], [("PROCEDURE",)])
    with tempfile.TemporaryDirectory() as td:
        conn = FakeConn(script)
        expect_exit(lambda: pythia.run_apply(
            conn, "APP", apply_ns(td),
            "CREATE OR REPLACE FUNCTION pkg_order RETURN NUMBER AS BEGIN RETURN 1; END;"),
            "PROCEDURE", "cannot change", "DROP")
        assert wrote_ddl(conn) == []
        assert pythia.list_journal_entries(td) == []   # a refusal leaves no entry


def test_naming_violation_rules():
    conv = {"naming": {"PROCEDURE": "^P_[A-Z0-9_]+$"}}
    assert pythia.naming_violation("PROCEDURE", "DO_STUFF", conv) is not None
    assert pythia.naming_violation("PROCEDURE", "P_DO_STUFF", conv) is None
    assert pythia.naming_violation("FUNCTION", "ANYTHING", conv) is None  # no pattern
    assert pythia.naming_violation("PROCEDURE", "DO_STUFF", None) is None  # no config


def test_conventions_file_is_validated():
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td) / ".pythia"
        d.mkdir()
        (d / "conventions.json").write_text('{"naming": {"PROCEDURE": "[bad"}}',
                                            encoding="utf-8")
        expect_exit(lambda: pythia.load_conventions(td), "PROCEDURE", "regex")
        (d / "conventions.json").write_text('{"nameing": {}}', encoding="utf-8")
        expect_exit(lambda: pythia.load_conventions(td), "nameing", "naming")
        (d / "conventions.json").unlink()
        assert pythia.load_conventions(td) is None


def test_apply_preview_warns_on_naming_drift():
    """A name outside the project's pattern warns at preview — style is a
    warning, policy is the thing that blocks."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td) / ".pythia"
        d.mkdir()
        (d / "conventions.json").write_text(
            '{"naming": {"PACKAGE BODY": "^XX_[A-Z0-9_]+$"}}', encoding="utf-8")
        conn = FakeConn(base_script())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = pythia.run_apply(conn, "APP", apply_ns(td, file="f.sql"), NEW_FILE)
        out = buf.getvalue()
        assert code == 0
        assert "naming" in out.lower() and "XX_" in out    # warned...
        assert len(pythia.list_journal_entries(td)) == 1   # ...but not blocked


def test_invocation_reflects_how_the_tool_was_run():
    import os
    interp = pathlib.Path(sys.executable).stem   # python / python3 / python3.13
    old = sys.argv[0]
    try:
        sys.argv[0] = "scripts/pythia.py"        # run from source
        assert pythia.invocation() == f"{interp} scripts/pythia.py"
        # packaged entry point — a native path for whichever OS runs the test
        # (backslash is not a separator on POSIX, so the Windows form belongs
        # only on Windows)
        if os.name == "nt":
            sys.argv[0] = r"C:\somewhere\pythia.exe"
            assert pythia.invocation() == "pythia.exe"
        else:
            sys.argv[0] = "/usr/local/bin/pythia"
            assert pythia.invocation() == "pythia"
    finally:
        sys.argv[0] = old


def test_preview_hint_is_pasteable():
    """The printed To-apply line must work when pasted verbatim — a bare
    `pythia ...` is CommandNotFound for anyone running from source."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        conn = FakeConn(base_script())
        buf = io.StringIO()
        old = sys.argv[0]
        try:
            sys.argv[0] = "scripts/pythia.py"
            with contextlib.redirect_stdout(buf):
                pythia.run_apply(conn, "APP", apply_ns(td, file="f.sql"), NEW_FILE)
        finally:
            sys.argv[0] = old
        interp = pathlib.Path(sys.executable).stem
        assert f"{interp} scripts/pythia.py apply f.sql --confirm " in buf.getvalue()


def test_preview_diff_ignores_the_create_header():
    """ALL_SOURCE never stores the CREATE OR REPLACE header, so a file
    identical to the database must not show a phantom two-line change."""
    import contextlib
    import io
    file_text = "CREATE OR REPLACE PACKAGE BODY pkg_order AS\n  old line;\nEND;\n/\n"
    with tempfile.TemporaryDirectory() as td:
        conn = FakeConn(base_script())          # db source == the same body
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pythia.run_apply(conn, "APP", apply_ns(td, file="f.sql"), file_text)
        out = buf.getvalue()
        assert "no source change" in out
        assert "lines changed" not in out


def test_apply_builds_plscope_index_by_default():
    """The write session sets plscope_settings before the CREATE, so every
    object applied through pythia carries the semantic index."""
    with tempfile.TemporaryDirectory() as td:
        conn = FakeConn(base_script())
        code = pythia.run_apply(conn, "APP", apply_ns(td, file="f.sql", yes=True),
                                NEW_FILE)
        assert code == 0
        stmts = [s for s, _ in conn.executed]
        alter = [i for i, s in enumerate(stmts) if "plscope_settings" in s.lower()]
        create = [i for i, s in enumerate(stmts)
                  if s.lstrip().lower().startswith("create")]
        assert alter and create and alter[0] < create[0]


def test_apply_plscope_opt_out_via_settings():
    with tempfile.TemporaryDirectory() as td:
        (pathlib.Path(td) / ".pythia").mkdir()
        (pathlib.Path(td) / ".pythia" / "settings.json").write_text(
            '{"plscope_on_apply": false}', encoding="utf-8")
        conn = FakeConn(base_script())
        code = pythia.run_apply(conn, "APP", apply_ns(td, file="f.sql", yes=True),
                                NEW_FILE)
        assert code == 0
        assert not any("plscope_settings" in s.lower()
                       for s, _ in conn.executed)


def test_privilege_warning_speaks_proxy():
    """A proxy session inheriting ANY privileges: the warning must blame the
    owner's grants, not the entrance; a clean proxy session warns not at all."""
    dangerous = {"from session_privs": ([("PRIVILEGE",)],
                                        [("DROP ANY TABLE",), ("SELECT ANY TABLE",)]),
                 "proxy_user": ([("P",)], [("CORE_BH_AGENT",)])}
    msg = pythia.privilege_warning(FakeConn(dangerous), "CORE_BH",
                                   "core_bh_agent[core_bh]")
    assert "Proxy session" in msg and "CORE_BH_AGENT" in msg
    assert "owner holds" in msg and "DROP ANY TABLE" in msg
    clean = {"from session_privs": ([("PRIVILEGE",)], []),
             "proxy_user": ([("P",)], [("CORE_BH_AGENT",)])}
    assert pythia.privilege_warning(FakeConn(clean), "CORE_BH",
                                    "core_bh_agent[core_bh]") is None
    # no proxy, direct owner: the original warning stands
    direct = {"from session_privs": ([("PRIVILEGE",)], [])}
    assert "schema owner" in pythia.privilege_warning(
        FakeConn(direct), "APP", "APP")


def test_prune_drops_only_redundant_previews():
    """A preview wrote nothing, but its restore.sql is the live version at
    that moment — the only rollback for a change run by hand. Prune may drop
    it only when a newer entry already keeps the identical rollback."""
    import argparse
    import contextlib
    import datetime
    import io
    with tempfile.TemporaryDirectory() as td:
        base = datetime.datetime(2026, 8, 27, 9, 0, 0)
        # two previews of the same object, same live version -> one is spare
        pythia.write_journal_entry(td, "PROCEDURE", "P_A", "old", "new1",
                                   {"applied": False}, now=base)
        keep_dup = pythia.write_journal_entry(
            td, "PROCEDURE", "P_A", "old", "new2", {"applied": False},
            now=base.replace(minute=1))
        # a preview of another object, unique rollback -> must survive
        lone = pythia.write_journal_entry(
            td, "PROCEDURE", "P_B", "only_copy", "x", {"applied": False},
            now=base.replace(minute=2))
        applied = pythia.write_journal_entry(
            td, "PROCEDURE", "P_C", "a", "b", {"applied": True},
            now=base.replace(minute=3))
        snap = pythia.write_journal_entry(
            td, "PROCEDURE", "P_D", "a", "a",
            {"snapshot": True, "applied": False}, now=base.replace(minute=4))
        ns = argparse.Namespace(action="prune", project_root=td, json=False,
                                id=None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pythia.cmd_journal(None, None, ns)
        left = pythia.list_journal_entries(td)
        assert "pruned 1" in buf.getvalue(), buf.getvalue()
        assert applied in left and snap in left      # never touched
        assert lone in left                          # unique rollback survives
        assert keep_dup in left                      # newest of the pair kept


def test_agent_user_alter_form_and_warning_prediction():
    """DB-aware agent-user: an existing agent user gets the ALTER+UNLOCK
    form (CREATE would be ORA-01920), and a dirty owner is called out —
    check will STILL warn — before the DBA runs anything."""
    import argparse
    import contextlib
    import io
    import os
    script = {"from all_users": ([("USERNAME",)], [("APP_AGENT",)]),
              "from session_privs": ([("PRIVILEGE",)], [("DROP ANY TABLE",)])}
    with tempfile.TemporaryDirectory() as td:
        (pathlib.Path(td) / ".pythia").mkdir()
        (pathlib.Path(td) / ".pythia" / "connections.json").write_text(
            json.dumps({"dev": {"host": "h", "user": "APP", "password": "p",
                                "schema": "APP"}}), encoding="utf-8")
        cwd = os.getcwd()
        os.chdir(td)
        try:
            ns = argparse.Namespace(conn=None, json=True, save=False, name=None)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                pythia.cmd_agent_user(FakeConn(script), "APP", ns)
            d = json.loads(buf.getvalue())
        finally:
            os.chdir(cwd)
        assert d["agent_user_exists"] is True
        assert "ALTER USER APP_AGENT IDENTIFIED BY" in d["sql"]
        assert "ACCOUNT UNLOCK" in d["sql"]
        assert "CREATE USER" not in d["sql"]
        assert d["check_will_warn"] is True
        assert d["owner_dangerous_privs"] == ["DROP ANY TABLE"]


class _NoTTY:
    """A headless agent session: PYTHIA_CI cleared, stdin not a terminal."""
    def __enter__(self):
        import io
        self.ci = os.environ.pop("PYTHIA_CI", None)
        self.stdin = sys.stdin
        sys.stdin = io.StringIO()
        return self

    def __exit__(self, *a):
        sys.stdin = self.stdin
        if self.ci is not None:
            os.environ["PYTHIA_CI"] = self.ci
        return False


def test_headless_yes_is_refused_before_anything_is_written():
    """--yes belongs to a human at a terminal. An agent (no TTY) must be
    told to preview, stop, and wait for the developer — and nothing may
    reach the database."""
    with tempfile.TemporaryDirectory() as td, _NoTTY():
        conn = FakeConn(base_script())
        try:
            pythia.run_apply(conn, "APP", apply_ns(td, file="f.sql", yes=True),
                             NEW_FILE)
        except SystemExit as e:
            msg = str(e)
            assert "developer" in msg and "--confirm" in msg
            assert "PYTHIA_CI" in msg
        else:
            raise AssertionError("expected SystemExit, none raised")
        assert wrote_ddl(conn) == []          # refused before the write
    # the developer at a real terminal keeps --yes (simulated via PYTHIA_CI)
    with tempfile.TemporaryDirectory() as td:
        conn = FakeConn(base_script())
        assert pythia.run_apply(conn, "APP",
                                apply_ns(td, file="f.sql", yes=True),
                                NEW_FILE) == 0


def test_headless_policy_loosening_is_refused_tightening_allowed():
    import argparse
    import contextlib
    import io

    def policy_ns(root, group, value):
        return argparse.Namespace(action="set", group=group, value=value,
                                  json=False, project_root=root)

    with tempfile.TemporaryDirectory() as td, _NoTTY():
        # deny -> confirm is loosening: the developer's decision
        try:
            pythia.cmd_policy(None, None, policy_ns(td, "structural", "confirm"))
        except SystemExit as e:
            assert "developer" in str(e) and "policy set structural" in str(e)
        else:
            raise AssertionError("expected SystemExit, none raised")
        assert not pythia.policy_path(td).is_file()   # nothing written
        # confirm -> deny is tightening: always allowed, even headless
        with contextlib.redirect_stdout(io.StringIO()):
            pythia.cmd_policy(None, None, policy_ns(td, "plsql_source", "deny"))
        assert pythia.load_policy(td)["plsql_source"][0] == "deny"


def test_report_admits_a_created_objects_undo_is_blocked():
    """pythia exists so nothing lies about rollback. Undoing a CREATE is a
    DROP, DROP is structural, and structural is deny by default — so the
    printed restore command would be refused. Say so, in the same breath."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        conn = FakeConn(base_script(db_source=""))     # object did not exist
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pythia.run_apply(conn, "APP", apply_ns(td, file="f.sql", yes=True),
                             NEW_FILE)
        out = buf.getvalue().lower()
        assert "dropping it" in out
        assert "will be refused" in out, out[-400:]
        assert "policy set structural confirm" in out
    # and once the developer has loosened it, the warning goes away
    with tempfile.TemporaryDirectory() as td:
        pathlib.Path(td, ".pythia").mkdir()
        pathlib.Path(td, ".pythia", "policy.json").write_text(
            '{"structural": "confirm"}', encoding="utf-8")
        conn = FakeConn(base_script(db_source=""))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pythia.run_apply(conn, "APP", apply_ns(td, file="f.sql", yes=True),
                             NEW_FILE)
        assert "will be refused" not in buf.getvalue().lower()


def test_auto_snapshot_captures_silently_and_writes_a_rollback_file():
    """Every captured version leaves a runnable rollback file — that is the
    whole point for work done by hand, outside pythia. And capturing costs
    the agent no context: nothing is printed when nothing moved."""
    import argparse
    import contextlib
    import io
    src = "PROCEDURE P_X AS\nBEGIN\n  NULL;\nEND;\n"
    script = {"from all_source": ([("TYPE", "LINE", "TEXT")],
                                  [("PROCEDURE", i + 1, ln + "\n")
                                   for i, ln in enumerate(src.splitlines())])}
    with tempfile.TemporaryDirectory() as td:
        ns = argparse.Namespace(project_root=td, conn_name="DEV")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            drift = pythia.auto_snapshot(FakeConn(script), "APP", "P_X", ns)
        assert drift is None                      # first sighting: a baseline
        assert buf.getvalue() == ""               # and not one token of output
        ids = pythia.list_journal_entries(td)
        assert len(ids) == 1
        e = pythia.read_journal_entry(td, ids[0])
        assert e["meta"]["snapshot"] is True
        rollback = e["restore"]
        assert rollback.startswith("CREATE OR REPLACE PROCEDURE P_X")
        assert (pythia.journal_root(td) / ids[0] / "restore.sql").is_file()

        # reading it again unchanged must not create a second entry
        assert pythia.auto_snapshot(FakeConn(script), "APP", "P_X", ns) is None
        assert pythia.list_journal_entries(td) == ids


def test_auto_snapshot_reports_drift_and_keeps_the_old_rollback():
    """Source moved with no apply of ours behind it — say so once, and point
    at the rollback file for the version that was there before."""
    import argparse
    old_src = "PROCEDURE P_X AS\nBEGIN\n  NULL;\nEND;\n"
    new_src = "PROCEDURE P_X AS\nBEGIN\n  other_thing;\nEND;\n"

    def script(text):
        return {"from all_source": ([("TYPE", "LINE", "TEXT")],
                                    [("PROCEDURE", i + 1, ln + "\n")
                                     for i, ln in enumerate(text.splitlines())])}
    with tempfile.TemporaryDirectory() as td:
        ns = argparse.Namespace(project_root=td, conn_name="DEV")
        pythia.auto_snapshot(FakeConn(script(old_src)), "APP", "P_X", ns)
        first = pythia.list_journal_entries(td)[0]
        drift = pythia.auto_snapshot(FakeConn(script(new_src)), "APP", "P_X", ns)
        assert drift and "changed outside pythia" in drift
        assert "restore.sql" in drift
        # the older version's rollback is still on disk and still runnable
        old_rollback = pythia.read_journal_entry(td, first)["restore"]
        assert "NULL;" in old_rollback
        assert len(pythia.list_journal_entries(td)) == 2


def test_grant_lifecycle_mint_read_validate():
    """A grant is minted for one token on one connection, is good once, and
    every way it can be wrong has its own status — the refusal messages in
    apply are built on these names."""
    import datetime
    with tempfile.TemporaryDirectory() as td:
        now = datetime.datetime(2026, 8, 27, 14, 3, 11)
        rec = pythia.mint_grant(td, "7f3a91", "escs_dev", now=now)
        assert rec["token"] == "7f3a91" and rec["conn"] == "escs_dev"
        assert rec["used_at"] is None
        assert rec["revalidate"] is None          # reserved for the DML spec
        assert (pythia.grants_root(td) / "7f3a91.json").is_file()

        g = pythia.read_grant(td, "7f3a91")
        assert g["minted_at"] == now.isoformat(timespec="seconds")
        assert pythia.grant_status(g, "escs_dev", now=now) == "ok"
        # inside the window
        soon = now + datetime.timedelta(minutes=14)
        assert pythia.grant_status(g, "escs_dev", now=soon) == "ok"
        # past it
        late = now + datetime.timedelta(minutes=16)
        assert pythia.grant_status(g, "escs_dev", now=late) == "expired"
        # a different connection with the same token is not this approval
        assert pythia.grant_status(g, "escs_test", now=now) == "wrong_conn"
        # absent
        assert pythia.read_grant(td, "nosuch") is None
        assert pythia.grant_status(None, "escs_dev", now=now) == "missing"

        pythia.spend_grant(td, "7f3a91", now=now)
        g2 = pythia.read_grant(td, "7f3a91")
        assert g2["used_at"] == now.isoformat(timespec="seconds")
        assert pythia.grant_status(g2, "escs_dev", now=now) == "spent"


def test_grant_conn_comparison_is_case_insensitive():
    """connections.json names are typed by humans; DEV and dev are one
    connection, and an approval must not hinge on how it was spelled."""
    import datetime
    with tempfile.TemporaryDirectory() as td:
        now = datetime.datetime(2026, 8, 27, 14, 0, 0)
        pythia.mint_grant(td, "aaa111", "DEV", now=now)
        g = pythia.read_grant(td, "aaa111")
        assert pythia.grant_status(g, "dev", now=now) == "ok"


def test_prune_expired_grants_removes_only_the_dead():
    import datetime
    with tempfile.TemporaryDirectory() as td:
        now = datetime.datetime(2026, 8, 27, 14, 0, 0)
        pythia.mint_grant(td, "old111", "DEV", now=now - datetime.timedelta(hours=2))
        pythia.mint_grant(td, "new222", "DEV", now=now)
        assert pythia.prune_expired_grants(td, now=now) == 1
        assert pythia.read_grant(td, "old111") is None
        assert pythia.read_grant(td, "new222") is not None


def test_read_grant_survives_a_corrupt_file():
    """A half-written or hand-edited file is not an approval, and must not
    crash the write path — it reads as missing, which refuses."""
    with tempfile.TemporaryDirectory() as td:
        pythia.grants_root(td).mkdir(parents=True)
        (pythia.grants_root(td) / "bad999.json").write_text("{not json",
                                                            encoding="utf-8")
        assert pythia.read_grant(td, "bad999") is None


def test_apply_with_token_but_no_grant_refuses_and_writes_nothing():
    """The token proves the content did not move. It does not prove a human
    approved. Without a grant the write does not happen — and the refusal
    hands over the exact approve line."""
    with tempfile.TemporaryDirectory() as td:
        tok = pythia.apply_token("PACKAGE BODY", "PKG_ORDER", NEW_FILE, OLD_SRC)
        conn = FakeConn(base_script())
        msg = expect_exit(
            lambda: pythia.run_apply(conn, "APP",
                                     apply_ns(td, file="f.sql", confirm=tok),
                                     NEW_FILE),
            "no developer approval", f"approve {tok}")
        assert "agents cannot run it" in msg.lower()
        assert wrote_ddl(conn) == []               # nothing reached the database


def test_apply_consumes_the_grant_and_records_it():
    """Happy path end to end: approve minted it, apply spends it, and the
    journal says how this write was authorized."""
    with tempfile.TemporaryDirectory() as td:
        tok = pythia.apply_token("PACKAGE BODY", "PKG_ORDER", NEW_FILE, OLD_SRC)
        pythia.mint_grant(td, tok, "DEV")
        conn = FakeConn(base_script())
        code = pythia.run_apply(conn, "APP",
                                apply_ns(td, file="f.sql", confirm=tok), NEW_FILE)
        assert code == 0 and len(wrote_ddl(conn)) == 1
        assert pythia.read_grant(td, tok)["used_at"] is not None   # spent
        meta = pythia.read_journal_entry(td, pythia.list_journal_entries(td)[0])["meta"]
        assert meta["confirmed_via"] == "grant"
        assert meta["grant_minted_at"]        # approve-to-apply latency is audit data


def test_apply_refuses_a_spent_grant_the_second_time():
    """Single use, proved end to end: the same approval cannot authorize two
    writes."""
    with tempfile.TemporaryDirectory() as td:
        tok = pythia.apply_token("PACKAGE BODY", "PKG_ORDER", NEW_FILE, OLD_SRC)
        pythia.mint_grant(td, tok, "DEV")
        conn = FakeConn(base_script())
        pythia.run_apply(conn, "APP", apply_ns(td, file="f.sql", confirm=tok),
                         NEW_FILE)
        assert len(wrote_ddl(conn)) == 1
        conn2 = FakeConn(base_script())
        expect_exit(lambda: pythia.run_apply(
            conn2, "APP", apply_ns(td, file="f.sql", confirm=tok), NEW_FILE),
            "already used")
        assert wrote_ddl(conn2) == []


def test_apply_refuses_expired_and_wrong_connection_grants():
    import datetime
    with tempfile.TemporaryDirectory() as td:
        tok = pythia.apply_token("PACKAGE BODY", "PKG_ORDER", NEW_FILE, OLD_SRC)
        # expired: minted two hours ago
        pythia.mint_grant(td, tok, "DEV",
                          now=datetime.datetime.now() - datetime.timedelta(hours=2))
        conn = FakeConn(base_script())
        expect_exit(lambda: pythia.run_apply(
            conn, "APP", apply_ns(td, file="f.sql", confirm=tok), NEW_FILE),
            "expired", f"approve {tok}")
        assert wrote_ddl(conn) == []
    with tempfile.TemporaryDirectory() as td:
        tok = pythia.apply_token("PACKAGE BODY", "PKG_ORDER", NEW_FILE, OLD_SRC)
        pythia.mint_grant(td, tok, "STAGING")          # approved elsewhere
        conn = FakeConn(base_script())
        expect_exit(lambda: pythia.run_apply(
            conn, "APP", apply_ns(td, file="f.sql", confirm=tok), NEW_FILE),
            "staging", "dev")
        assert wrote_ddl(conn) == []


def test_apply_yes_at_a_console_still_needs_no_grant():
    """--yes is a human at a real terminal previewing and applying in one
    motion — that IS the approval act. Unchanged by this release."""
    with tempfile.TemporaryDirectory() as td:
        conn = FakeConn(base_script())
        code = pythia.run_apply(conn, "APP", apply_ns(td, file="f.sql", yes=True),
                                NEW_FILE)
        assert code == 0 and len(wrote_ddl(conn)) == 1
        assert not pythia.grants_root(td).exists()     # no grant was needed


def test_preview_prints_both_follow_up_lines():
    """Two steps, two people: the developer's approve line and the agent's
    confirm line, both pasteable."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        conn = FakeConn(base_script())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pythia.run_apply(conn, "APP", apply_ns(td, file="f.sql"), NEW_FILE)
        out = buf.getvalue()
        tok = pythia.apply_token("PACKAGE BODY", "PKG_ORDER", NEW_FILE, OLD_SRC)
        assert f"approve --card {tok}" in out      # the chat door
        assert f"approve {tok}" in out             # the console door
        assert f"apply f.sql --confirm {tok}" in out
        assert "in chat" in out and "terminal" in out


def test_journal_restore_needs_a_grant_too():
    """One door for writes — restore goes through run_apply, so the gate
    covers it. Asserted, not assumed."""
    with tempfile.TemporaryDirectory() as td:
        eid = pythia.write_journal_entry(td, "PACKAGE BODY", "PKG_ORDER",
                                         OLD_SRC, NEW_FILE, {"connection": "DEV"})
        db_now = "PACKAGE BODY pkg_order AS\n  new line;\nEND;\n"
        conn = FakeConn(base_script(db_source=db_now))
        ns = apply_ns(td)
        ns.command, ns.action, ns.id, ns.file = "journal", "restore", eid, f"journal:{eid}"
        ns.confirm = pythia.apply_token(
            "PACKAGE BODY", "PKG_ORDER",
            pythia.read_journal_entry(td, eid)["restore"], db_now)
        expect_exit(lambda: pythia.run_restore(conn, "APP", ns),
                    "no developer approval")
        assert wrote_ddl(conn) == []


def test_apply_json_reports_the_grant_refusal_shape():
    """--json callers must be able to tell 'needs approval' from 'stale
    token' without scraping prose."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        tok = pythia.apply_token("PACKAGE BODY", "PKG_ORDER", NEW_FILE, OLD_SRC)
        conn = FakeConn(base_script())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            expect_exit(lambda: pythia.run_apply(
                conn, "APP", apply_ns(td, file="f.sql", confirm=tok, json=True),
                NEW_FILE), "no developer approval")
        assert wrote_ddl(conn) == []


def approve_ns(root, token, **kw):
    import argparse
    ns = argparse.Namespace(token=token, json=False, project_root=root,
                            command="approve", conn=None, conn_name=None,
                            limit=200, max_lines=2000, offset=0, raw=False,
                            color=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_approve_is_refused_without_a_real_console():
    """approve is the human's act. A headless agent must not be able to mint
    its own approval — and PYTHIA_CI does not open this door, because CI
    already has --yes."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td, _NoTTY():
        pythia.write_journal_entry(td, "PACKAGE BODY", "PKG_ORDER", OLD_SRC,
                                   NEW_FILE, {"token": "7f3a91",
                                              "connection": "DEV",
                                              "applied": False})
        with contextlib.redirect_stdout(io.StringIO()):
            expect_exit(lambda: pythia.cmd_approve(None, None,
                                                   approve_ns(td, "7f3a91")),
                        "developer's command", "real console")
        assert not pythia.grants_root(td).exists()      # nothing was minted
    # PYTHIA_CI is set for the suite; it must not be a way in either
    with tempfile.TemporaryDirectory() as td:
        old_stdin, sys.stdin = sys.stdin, io.StringIO()
        try:
            pythia.write_journal_entry(td, "PACKAGE BODY", "PKG_ORDER", OLD_SRC,
                                       NEW_FILE, {"token": "7f3a91",
                                                  "connection": "DEV",
                                                  "applied": False})
            with contextlib.redirect_stdout(io.StringIO()):
                expect_exit(lambda: pythia.cmd_approve(
                    None, None, approve_ns(td, "7f3a91")), "real console")
        finally:
            sys.stdin = old_stdin
        assert not pythia.grants_root(td).exists()


def test_approve_refuses_a_token_no_preview_carries():
    """Blind-approving a bare hash is forbidden: a human approves a thing,
    not a string."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        with contextlib.redirect_stdout(io.StringIO()):
            expect_exit(lambda: pythia.cmd_approve(
                None, None, approve_ns(td, "abc123", console=True)),
                "no pending preview", "apply")
        assert not pythia.grants_root(td).exists()


def test_approve_shows_the_object_and_mints_the_grant():
    """What the human sees comes from the journal entry the preview wrote —
    object, schema, impact, connection — not from a recomputation."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        pythia.write_journal_entry(
            td, "PACKAGE BODY", "PKG_ORDER", OLD_SRC, NEW_FILE,
            {"token": "7f3a91", "connection": "DEV", "schema": "APP",
             "group": "plsql_source", "applied": False,
             "summary": "12 dependent objects, 11 currently VALID"})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pythia.cmd_approve(None, None, approve_ns(td, "7f3a91", console=True))
        out = buf.getvalue()
        assert "PKG_ORDER" in out and "PACKAGE BODY" in out
        assert "APP" in out and "DEV" in out
        assert "12 dependent objects" in out
        assert "single use" in out.lower()
        assert "--confirm 7f3a91" in out          # the line the agent runs
        g = pythia.read_grant(td, "7f3a91")
        assert g and g["conn"] == "DEV" and g["used_at"] is None


def test_approve_binds_to_the_previews_connection_not_a_flag():
    """A mistyped --conn must not bind an approval to a database the preview
    never ran on: the connection is copied from the journal entry."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        pythia.write_journal_entry(td, "PROCEDURE", "P_X", "old", "new",
                                   {"token": "bb2222", "connection": "DEV",
                                    "schema": "APP", "applied": False})
        with contextlib.redirect_stdout(io.StringIO()):
            pythia.cmd_approve(None, None,
                               approve_ns(td, "bb2222", console=True,
                                          conn="STAGING", conn_name="STAGING"))
        assert pythia.read_grant(td, "bb2222")["conn"] == "DEV"


def test_approve_of_an_unsnapshotable_group_says_so():
    """Non-plsql_source confirm-mode groups have no object identity and no
    snapshot. Approve shows the statement and the honest warning instead of
    pretending there is an undo."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        pythia.write_journal_entry(
            td, "DATA_DML", "STATEMENT", "", "delete from t_order where id = 7",
            {"token": "cc3333", "connection": "DEV", "schema": "APP",
             "group": "data_dml", "applied": False})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pythia.cmd_approve(None, None, approve_ns(td, "cc3333", console=True))
        out = buf.getvalue().lower()
        assert "delete from t_order" in out
        assert "no snapshot" in out


def test_approve_json_shape():
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        pythia.write_journal_entry(td, "PROCEDURE", "P_X", "old", "new",
                                   {"token": "dd4444", "connection": "DEV",
                                    "schema": "APP", "applied": False})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pythia.cmd_approve(None, None, approve_ns(td, "dd4444",
                                                      console=True, json=True))
        d = json.loads(buf.getvalue())
        assert d["ok"] is True and d["token"] == "dd4444"
        assert d["conn"] == "DEV" and d["object"] == "P_X"
        assert d["expires_at"] and d["ttl_minutes"] == pythia.GRANT_TTL_MINUTES


def test_approve_prunes_expired_grants_on_the_way_past():
    import contextlib
    import datetime
    import io
    with tempfile.TemporaryDirectory() as td:
        pythia.mint_grant(td, "old111", "DEV",
                          now=datetime.datetime.now() - datetime.timedelta(hours=2))
        pythia.write_journal_entry(td, "PROCEDURE", "P_X", "old", "new",
                                   {"token": "ee5555", "connection": "DEV",
                                    "schema": "APP", "applied": False})
        with contextlib.redirect_stdout(io.StringIO()):
            pythia.cmd_approve(None, None, approve_ns(td, "ee5555", console=True))
        assert pythia.read_grant(td, "old111") is None
        assert pythia.read_grant(td, "ee5555") is not None


def test_approve_refuses_a_preview_that_was_already_applied():
    """A spent preview's token can never match a future apply, so minting a
    grant for it would only hand back a refusal one step later."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        pythia.write_journal_entry(td, "PROCEDURE", "P_X", "old", "new",
                                   {"token": "ff6666", "connection": "DEV",
                                    "schema": "APP", "applied": True})
        with contextlib.redirect_stdout(io.StringIO()):
            expect_exit(lambda: pythia.cmd_approve(
                None, None, approve_ns(td, "ff6666", console=True)),
                "already applied")
        assert pythia.read_grant(td, "ff6666") is None


def test_approve_needs_no_database():
    """approve is a filesystem act — it must work in a developer's terminal
    that has no connection configured at all."""
    assert "approve" in pythia.NO_DB_COMMANDS
    assert "approve" in pythia.COMMANDS


# --- the chat door: approve --card / approve --hook ---------------------------

def _hook_payload(question, answer, tool="AskUserQuestion", session="sess-1"):
    q = {"question": question, "header": "pythia", "multiSelect": False,
         "options": [{"label": "Approve", "description": "mint"},
                     {"label": "Reject", "description": "stop"}]}
    return {"hook_event_name": "PostToolUse", "session_id": session,
            "tool_name": tool, "tool_input": {"questions": [q]},
            "tool_response": {"questions": [q], "answers": {question: answer}}}


def _run_hook(td, payload):
    """A headless agent session with the hook payload on stdin — exactly how
    Claude Code invokes it. Returns stdout."""
    import contextlib
    import io
    buf = io.StringIO()
    with _NoTTY():
        sys.stdin = io.StringIO(json.dumps(payload) if not isinstance(payload, str)
                                else payload)
        with contextlib.redirect_stdout(buf):
            pythia.cmd_approve(None, None, approve_ns(td, [], hook=True))
    return buf.getvalue()


def _pending_preview(td, token="7f3a91"):
    pythia.write_journal_entry(
        td, "PACKAGE BODY", "PKG_ORDER", OLD_SRC, NEW_FILE,
        {"token": token, "connection": "DEV", "schema": "APP",
         "group": "plsql_source", "applied": False,
         "summary": "12 dependent objects, 11 currently VALID"})
    _, _, body = pythia.approval_card(td, token)
    return f"pythia approve {token}\n" + "\n".join(body)


def test_hook_mints_the_grant_the_developer_approved_in_chat():
    """The developer answered Approve to a question carrying pythia's own
    card: that is the console act, through the other door. The grant says
    which door, and which session."""
    with tempfile.TemporaryDirectory() as td:
        card = _pending_preview(td)
        out = _run_hook(td, _hook_payload("  Please approve:\n" + card, "Approve"))
        g = pythia.read_grant(td, "7f3a91")
        assert g and g["approver"] == "chat" and g["session"] == "sess-1"
        assert g["conn"] == "DEV" and g["used_at"] is None
        ctx = json.loads(out)["hookSpecificOutput"]
        assert ctx["hookEventName"] == "PostToolUse"
        assert "--confirm 7f3a91" in ctx["additionalContext"]


def test_hook_refuses_a_question_that_is_not_pythias_card():
    """An agent's paraphrase is not a preview. If the question did not carry
    the card verbatim, the human approved the agent's words — no grant, and
    the agent is told to ask again with the card."""
    with tempfile.TemporaryDirectory() as td:
        _pending_preview(td)
        out = _run_hook(td, _hook_payload(
            "pythia approve 7f3a91\nJust a tiny, totally safe change.", "Approve"))
        assert pythia.read_grant(td, "7f3a91") is None
        assert "approve --card 7f3a91" in json.loads(out)["hookSpecificOutput"]["additionalContext"]


def test_hook_mints_nothing_on_reject_other_tools_or_garbage():
    with tempfile.TemporaryDirectory() as td:
        card = _pending_preview(td)
        out = _run_hook(td, _hook_payload(card, "Reject"))
        assert pythia.read_grant(td, "7f3a91") is None
        assert "did not approve" in out.lower()
        assert _run_hook(td, _hook_payload(card, "Approve", tool="Bash")) == ""
        assert _run_hook(td, "not json at all") == ""
        assert _run_hook(td, _hook_payload("no token here", "Approve")) == ""
        assert not pythia.grants_root(td).exists()
        # a token no preview carries cannot be approved blind, in chat either
        out = _run_hook(td, _hook_payload("pythia approve abc123", "Approve"))
        assert "no pending preview" in out.lower()
        assert pythia.read_grant(td, "abc123") is None


def test_approve_card_needs_no_console_and_mints_nothing():
    """--card is the agent's half: it prints what to ask with. Headless is
    fine, and nothing is minted by printing."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td, _NoTTY():
        _pending_preview(td)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pythia.cmd_approve(None, None, approve_ns(td, ["7f3a91"], card=True))
        out = buf.getvalue()
        assert "approve 7f3a91" in out and "PKG_ORDER" in out
        assert "12 dependent objects" in out and "DEV" in out
        assert "\x1b[" not in out                       # plain text: it goes into a question
        assert not pythia.grants_root(td).exists()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pythia.cmd_approve(None, None, approve_ns(td, ["7f3a91"], card=True,
                                                      json=True))
        d = json.loads(buf.getvalue())
        assert d["minted"] is False and d["question"]["header"] == "pythia"
        assert [o["label"] for o in d["question"]["options"]] == ["Approve", "Reject"]
        assert d["card"] in d["question"]["question"]


def test_approve_several_tokens_in_one_console_act():
    """Four previews, one approve line — not four."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        _pending_preview(td, "aa1111")
        _pending_preview(td, "bb2222")
        with contextlib.redirect_stdout(io.StringIO()):
            pythia.cmd_approve(None, None,
                               approve_ns(td, ["aa1111", "bb2222"], console=True))
        assert pythia.read_grant(td, "aa1111")["approver"] == "console"
        assert pythia.read_grant(td, "bb2222")["approver"] == "console"


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
