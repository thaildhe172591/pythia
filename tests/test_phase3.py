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


def test_journal_prune_keeps_applied_entries():
    import argparse
    import datetime
    with tempfile.TemporaryDirectory() as td:
        base = datetime.datetime(2026, 8, 26, 12, 0, 0)
        pythia.write_journal_entry(td, "PROCEDURE", "P_PREVIEW", "a", "b",
                                   {"applied": False}, now=base)
        kept = pythia.write_journal_entry(
            td, "PROCEDURE", "P_APPLIED", "a", "b", {"applied": True},
            now=base.replace(minute=1))
        ns = argparse.Namespace(action="prune", project_root=td, json=False,
                                id=None)
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pythia.cmd_journal(None, None, ns)
        assert "pruned 1" in buf.getvalue()
        assert pythia.list_journal_entries(td) == [kept]


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


def test_prune_never_eats_a_snapshot():
    import argparse
    import contextlib
    import io
    import datetime
    with tempfile.TemporaryDirectory() as td:
        base = datetime.datetime(2026, 8, 27, 9, 0, 0)
        pythia.write_journal_entry(td, "PROCEDURE", "P_A", "a", "b",
                                   {"applied": False}, now=base)
        snap = pythia.write_journal_entry(
            td, "PROCEDURE", "P_B", "a", "a", {"snapshot": True,
                                               "applied": False},
            now=base.replace(minute=1))
        ns = argparse.Namespace(action="prune", project_root=td, json=False,
                                id=None)
        with contextlib.redirect_stdout(io.StringIO()):
            pythia.cmd_journal(None, None, ns)
        assert pythia.list_journal_entries(td) == [snap]


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
