#!/usr/bin/env python3
"""Phase 6 self-checks — scripts, comments, lint, autopilot, deploy, hooks,
doctor. No database: the write path runs against the same fake connection
tests/test_phase3.py proves the safety properties with.

Run: python tests/test_phase6.py
"""
import contextlib
import datetime
import io
import json
import os
import pathlib
import sys
import tempfile

os.environ.setdefault("PYTHIA_CI", "1")   # the suites are headless by design
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE))
import pythia  # noqa: E402
import test_phase3 as p3  # noqa: E402  (fakes only; its tests are not re-run)

FakeConn, base_script, apply_ns, expect_exit = (
    p3.FakeConn, p3.base_script, p3.apply_ns, p3.expect_exit)


def quiet(fn, *a, **kw):
    """Run fn with stdout captured; returns (result, output)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **kw)
    return r, buf.getvalue()


def policy(root, **groups):
    d = pathlib.Path(root) / ".pythia"
    d.mkdir(exist_ok=True)
    (d / "policy.json").write_text(json.dumps(groups), encoding="utf-8")


def last_meta(root):
    return pythia.read_journal_entry(
        root, pythia.list_journal_entries(root)[0])["meta"]


def executed_ddl(conn, prefix):
    return [s for s, _ in conn.executed if s.lstrip().lower().startswith(prefix)]


# --- splitting scripts ---------------------------------------------------------

SCRIPT = """-- release 12
set define off
prompt creating the order table
create table t_order (
  id   number constraint pk_order primary key,
  note varchar2(100 char) default 'a;b'
);
comment on table t_order is 'Orders; one row each';
comment on column t_order.id is q'[it's the id; really]';
create or replace package body pkg_order as
  procedure p is begin null; end;
end pkg_order;
/
insert into t_order (id) values (1); insert into t_order (id) values (2);
/* a block comment ; with a semicolon */
drop table t_x purge;
"""


def test_split_script_follows_sqlplus_semantics():
    st, skipped = pythia.split_script(SCRIPT)
    groups = [pythia.classify(s["text"]) for s in st]
    assert groups == ["structural", "comments", "comments", "plsql_source",
                      "data_dml", "data_dml", "structural"], groups
    assert [s["line"] for s in st] == [4, 8, 9, 10, 14, 14, 15]
    assert st[0]["text"].endswith(")")              # the ; is not kept for SQL
    assert "'a;b'" in st[0]["text"]                 # ; inside a literal stays
    assert "q'[it's the id; really]'" in st[2]["text"]
    assert st[3]["text"].rstrip().endswith("end pkg_order;")   # block keeps END;
    assert [x["line"] for x in skipped] == [2, 3]   # directives, not SQL


def test_split_script_refuses_includes_and_keeps_line_numbers_exact():
    expect_exit(lambda: pythia.split_script("@other.sql\n"), "include")
    st, _ = pythia.split_script("\n\n-- head\ncreate table a (x number)\n;\n")
    assert st[0]["line"] == 3 and pythia.classify(st[0]["text"]) == "structural"
    # a block ends only at a lone slash, even with ; and / inside literals
    st, _ = pythia.split_script(
        "create or replace procedure p as\n  v varchar2(9) := '/';\nbegin\n"
        "  null;\nend;\n/\nselect 1 from dual;\n")
    assert len(st) == 2 and "'/'" in st[0]["text"]


def test_mask_sql_blanks_literals_and_comments_at_the_same_length():
    src = "x := 'a''b' || q'{c'd}' -- tail\n/* y */ z"
    m = pythia.mask_sql(src)
    assert len(m) == len(src) and m.count("\n") == 1
    assert "a" not in m.split(":=")[1].split("||")[0]
    assert "tail" not in m and "y" not in m and m.rstrip().endswith("z")


# --- the comments group -----------------------------------------------------------

def test_comment_on_is_its_own_group_with_a_parsed_target():
    assert pythia.classify("COMMENT ON COLUMN t_order.id IS 'x'") == "comments"
    assert pythia.classify("comment on table app.t_order is 'x'") == "comments"
    assert pythia.classify("comment on materialized view mv is 'x'") == "structural"
    assert pythia.parse_comment_target(
        'comment on column app.t_order."Mixed" is \'x\'') == (
        "column", "APP", "T_ORDER", "Mixed")
    assert pythia.parse_comment_target("comment on table t is 'x'") == (
        "table", None, "T", None)
    assert pythia.render_comment("column", "T", "Mixed", "it's") == \
        "COMMENT ON COLUMN T.\"Mixed\" IS 'it''s'\n"
    assert pythia.POLICY_DEFAULTS["comments"] == "confirm"


def test_prepare_statement_reads_a_semicolon_in_a_literal_as_text():
    out = pythia.prepare_statement("comment on table t is 'a; b';\n", "comments")
    assert out == "comment on table t is 'a; b'"
    expect_exit(lambda: pythia.prepare_statement(
        "comment on table t is 'a'; comment on table u is 'b';", "comments"),
        "one statement")


def comment_script(old="Old text"):
    s = base_script()
    s["from all_col_comments"] = ([("COMMENTS",)], [(old,)] if old is not None else [])
    return s


def test_comment_preview_snapshots_the_old_comment_and_restores_it():
    with tempfile.TemporaryDirectory() as td:
        text = "COMMENT ON COLUMN t_order.note IS 'Free text from the customer'\n"
        code, out = quiet(pythia.run_apply, FakeConn(comment_script()), "APP",
                          apply_ns(td, file="c.sql"), text)
        assert code == 0
        e = pythia.read_journal_entry(td, pythia.list_journal_entries(td)[0])
        assert e["meta"]["group"] == "comments"
        assert e["meta"]["object"] == "T_ORDER.NOTE"
        assert e["before"] == "Old text"
        assert e["restore"] == "COMMENT ON COLUMN T_ORDER.NOTE IS 'Old text'\n"
        assert "-COMMENT ON COLUMN T_ORDER.NOTE IS 'Old text'" in out


def test_comment_apply_writes_and_says_how_to_undo():
    with tempfile.TemporaryDirectory() as td:
        text = "COMMENT ON COLUMN t_order.note IS 'New'\n"
        quiet(pythia.run_apply, FakeConn(comment_script()), "APP",
              apply_ns(td, file="c.sql"), text)
        tok = last_meta(td)["token"]
        pythia.mint_grant(td, tok, "DEV")
        conn = FakeConn(comment_script())
        code, out = quiet(pythia.run_apply, conn, "APP",
                          apply_ns(td, file="c.sql", confirm=tok), text)
        assert code == 0
        assert executed_ddl(conn, "comment on column")
        assert "journal restore" in out and "previous comment" in out


def test_comment_card_promises_a_real_undo():
    with tempfile.TemporaryDirectory() as td:
        quiet(pythia.run_apply, FakeConn(comment_script()), "APP",
              apply_ns(td, file="c.sql"),
              "COMMENT ON TABLE t_order IS 'Orders'\n")
        _, _, body = pythia.approval_card(td, last_meta(td)["token"])
        text = "\n".join(body)
        assert "snapshotted" in text and "no snapshot" not in text


# --- destructive statements ---------------------------------------------------

def test_destructive_reason_names_what_cannot_come_back():
    d = pythia.destructive_reason
    assert "Recycle Bin" in d("DROP TABLE t PURGE", "structural")
    assert d("drop table t", "structural")
    assert d("truncate table t", "structural")
    assert d("alter table t drop column x", "structural")
    assert d("alter table t rename column a to b", "structural")
    assert d("alter table t set unused (x)", "structural")
    assert d("delete from t", "data_dml") and d("update t set x = 1", "data_dml")
    assert d("revoke select on t from u", "grants")
    assert d("alter table t add (x number)", "structural") is None
    assert d("create table t (x number)", "structural") is None
    assert d("delete from t where id = 1", "data_dml") is None
    assert d("create or replace procedure p as begin null; end;",
             "plsql_source") is None
    assert d("alter table t add (note varchar2(9) default 'drop')",
             "structural") is None             # a literal is not a keyword


def test_a_destructive_statement_is_flagged_in_preview_and_card():
    with tempfile.TemporaryDirectory() as td:
        policy(td, structural="confirm")
        _, out = quiet(pythia.run_apply, FakeConn(base_script()), "APP",
                       apply_ns(td, file="d.sql"), "drop table t_old\n")
        assert "DESTRUCTIVE" in out
        _, _, body = pythia.approval_card(td, last_meta(td)["token"])
        assert any("DESTRUCTIVE" in ln for ln in body)


# --- lint -------------------------------------------------------------------------

def rules(findings):
    return {f[0] if isinstance(f, tuple) else f["rule"] for f in findings}


def test_lint_catches_the_plsql_antipatterns():
    src = """CREATE OR REPLACE PROCEDURE p_bad(p_out IN OUT CLOB) AS
  v_name VARCHAR2(50);
BEGIN
  FOR r IN (SELECT id FROM t_order WHERE status = 'NEW') LOOP
    UPDATE t_order SET status = 'DONE' WHERE id = r.id;
    COMMIT;
  END LOOP;
  EXECUTE IMMEDIATE 'DELETE FROM t_log WHERE id = ' || v_name;
  IF v_name = '' THEN NULL; END IF;
  RAISE_APPLICATION_ERROR(-1, 'x');
  SELECT * INTO v_name FROM dual;
  v_name := 'Số tiền';
EXCEPTION
  WHEN OTHERS THEN NULL;
END p_bad;
"""
    got = rules(pythia.lint_statement(src, "plsql_source"))
    for r in ("when-others-null", "dynamic-sql-concat", "commit-in-loop",
              "row-by-row-dml", "empty-string-compare", "raise-app-error-range",
              "select-star", "non-ascii-literal", "hardcoded-size",
              "inout-lob-nocopy"):
        assert r in got, f"lint blind to {r}: {sorted(got)}"


def test_lint_stays_quiet_on_clean_code():
    src = """CREATE OR REPLACE PROCEDURE p_good(p_id IN t_order.id%TYPE) AS
  v_name t_order.note%TYPE;
BEGIN
  SELECT note INTO v_name FROM t_order WHERE id = p_id;
  EXECUTE IMMEDIATE 'DELETE FROM t_log WHERE id = :1' USING p_id;
EXCEPTION
  WHEN NO_DATA_FOUND THEN
    RAISE_APPLICATION_ERROR(-20001, 'order not found');
  WHEN OTHERS THEN
    log_error(SQLCODE, SQLERRM);
    RAISE;
END p_good;
"""
    assert pythia.lint_statement(src, "plsql_source") == []


def test_lint_knows_the_sanctioned_forms():
    assert "dynamic-sql-concat" not in rules(pythia.lint_statement(
        "create or replace procedure p(in_t varchar2) as begin "
        "execute immediate 'truncate table ' || dbms_assert.sql_object_name(in_t); "
        "end;", "plsql_source"))
    trig = ("create or replace trigger trg_x after insert on t for each row "
            "begin insert into t_log values (1); commit; end;")
    assert "commit-in-trigger" in rules(pythia.lint_statement(trig, "plsql_source"))
    body = ("create or replace package body pkg as procedure p is begin "
            "commit; end; end;")
    assert "commit-in-trigger" not in rules(pythia.lint_statement(body, "plsql_source"))


def test_lint_reads_tables_against_the_ddl_standards():
    f = rules(pythia.lint_statement(
        "create table t_note (id number, body varchar2(200), "
        "ref_id number references t_order(id))", "structural"))
    assert {"table-no-pk", "constraint-unnamed",
            "varchar2-byte-semantics"} <= f
    f = rules(pythia.lint_statement(
        "alter table t_order add (status varchar2(10 char) not null)",
        "structural"))
    assert "add-not-null-no-default" in f
    assert "add-not-null-no-default" not in rules(pythia.lint_statement(
        "alter table t_order add (status varchar2(10 char) default 'N' not null)",
        "structural"))
    assert "dml-no-where" in rules(pythia.lint_statement("delete from t", "data_dml"))
    assert "grant-to-public" in rules(pythia.lint_statement(
        "grant select on t to public", "grants"))


def test_lint_script_wants_every_new_column_commented_and_fks_indexed():
    script = """create table t_line (
  id       number constraint pk_line primary key,
  order_id number constraint fk_line_order references t_order(id),
  qty      number,
  constraint fk_line_order2 foreign key (order_id) references t_order(id)
);
comment on table t_line is 'Order lines';
comment on column t_line.id is 'Key';
"""
    f = pythia.lint_script(script)
    miss = [x for x in f if x["rule"] == "comment-missing"]
    assert miss and "ORDER_ID" in miss[0]["message"] and "QTY" in miss[0]["message"]
    assert any(x["rule"] == "fk-no-index" for x in f)
    fixed = script + ("comment on column t_line.order_id is 'Order';\n"
                      "comment on column t_line.qty is 'Quantity';\n"
                      "create index ix_line_order on t_line (order_id);\n")
    assert not [x for x in pythia.lint_script(fixed)
                if x["rule"] in ("comment-missing", "fk-no-index")]


def test_the_lead_tunes_lint_through_conventions_json():
    conv = {"naming": {"TABLE": "^T_[A-Z0-9_]+$"},
            "lint": {"rules": {"varchar2-byte-semantics": "error",
                               "table-no-pk": "off"},
                     "required_columns": ["CREATED_BY", "CREATED_AT"],
                     "param_prefix": "^P_",
                     "forbid": [{"id": "no-sysdate", "pattern": r"\bSYSDATE\b",
                                 "message": "use SYSTIMESTAMP",
                                 "severity": "error"}]}}
    f = pythia.lint_findings("create table orders (id number, n varchar2(9), "
                             "d date default sysdate)", "structural", conv)
    by = {x["rule"]: x for x in f}
    assert by["varchar2-byte-semantics"]["severity"] == "error"
    assert "table-no-pk" not in by
    assert "CREATED_BY" in by["required-column-missing"]["message"]
    assert by["forbid:no-sysdate"]["severity"] == "error"
    assert "naming" in by
    f = pythia.lint_findings("create or replace procedure p_x(id number) as "
                             "begin null; end;", "plsql_source", conv)
    assert any(x["rule"] == "param-prefix" for x in f)
    expect_exit(lambda: pythia.lint_config({"lint": {"rules": {"nope": "error"}}}),
                "nope")
    expect_exit(lambda: pythia.lint_config({"lint": {"typo": 1}}), "typo")
    expect_exit(lambda: pythia.lint_config({"lint": {"forbid": [{}]}}), "id")


def test_conventions_json_accepts_a_lint_block():
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td) / ".pythia"
        d.mkdir()
        (d / "conventions.json").write_text(json.dumps(
            {"naming": {}, "lint": {"require_comments": "error"}}),
            encoding="utf-8")
        conv = pythia.load_conventions(td)
        assert conv["lint"]["require_comments"] == "error"


def test_apply_preview_shows_lint_findings():
    with tempfile.TemporaryDirectory() as td:
        bad = ("CREATE OR REPLACE PACKAGE BODY pkg_order AS\n"
               "  PROCEDURE p IS BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END;\n"
               "END;\n/\n")
        _, out = quiet(pythia.run_apply, FakeConn(base_script()), "APP",
                       apply_ns(td, file="b.sql"), bad)
        assert "when-others-null" in out and "lint:" in out
        assert last_meta(td)["lint"][0]["rule"] == "when-others-null"


def test_cmd_lint_exits_2_on_errors():
    import argparse
    with tempfile.TemporaryDirectory() as td:
        f = pathlib.Path(td) / "x.sql"
        f.write_text("delete from t_order;\n", encoding="utf-8")
        ns = argparse.Namespace(paths=[str(f)], json=True, project_root=td,
                                color=False)
        try:
            _, out = quiet(pythia.cmd_lint, None, None, ns)
            raise AssertionError("expected exit 2")
        except SystemExit as e:
            assert e.code == 2
        f.write_text("delete from t_order where id = 1;\n", encoding="utf-8")
        _, out = quiet(pythia.cmd_lint, None, None, ns)
        assert json.loads(out)["errors"] == 0


# --- autopilot ----------------------------------------------------------------------

NOW = datetime.datetime(2026, 10, 14, 9, 0, 0)


def lease(td, groups=("plsql_source", "comments"), conn="DEV", **kw):
    req = pythia.new_lease_request(td, conn, {}, groups=list(groups), **kw)
    return pythia.activate_lease(td, req, "console")


def test_a_protected_connection_is_never_leased():
    with tempfile.TemporaryDirectory() as td:
        expect_exit(lambda: pythia.new_lease_request(
            td, "PROD", {"protected": True}), "protected")
        expect_exit(lambda: pythia.new_lease_request(
            td, "LIVE", {"env": "production"}), "protected")
        expect_exit(lambda: pythia.new_lease_request(td, "DEV", {}, hours=48),
                    "hours")


def test_the_lease_card_names_every_consequence():
    with tempfile.TemporaryDirectory() as td:
        req = pythia.new_lease_request(td, "DEV", {}, groups=["plsql_source",
                                                              "structural"],
                                       note="build the order module")
        text = "\n".join(pythia.lease_card(req, td))
        assert "DEV" in text and "8 hours" in text and "200 writes" in text
        assert "NO real undo" in text                 # structural, honestly
        assert "overrides it" in text                 # structural is deny
        assert "still need your approval" in text     # destructive excluded
        assert "build the order module" in text
        assert len(req["token"]) == 8


def test_an_active_lease_lands_a_covered_write_without_confirm():
    with tempfile.TemporaryDirectory() as td:
        lease(td)
        conn = FakeConn(base_script())
        code, out = quiet(pythia.run_apply, conn, "APP",
                          apply_ns(td, file="b.sql"), p3.NEW_FILE)
        assert code == 0 and executed_ddl(conn, "create or replace")
        m = last_meta(td)
        assert m["confirmed_via"] == "autopilot" and m["applied"]
        assert m["autopilot"] == pythia.read_lease(td)["token"]
        assert pythia.read_lease(td)["writes"] == 1
        assert "autopilot" in out


def test_a_lease_never_covers_another_connection_group_or_destruction():
    with tempfile.TemporaryDirectory() as td:
        lease(td, groups=("plsql_source", "structural"))
        policy(td, structural="confirm")
        for ns_kw, text, why in (
                ({"conn_name": "TEST"}, p3.NEW_FILE, "connection"),
                ({}, "grant select on t to u\n", "does not cover"),
                ({}, "drop table t_old\n", "destructive")):
            conn = FakeConn(base_script())
            if why == "does not cover":
                policy(td, structural="confirm", grants="confirm")
            _, out = quiet(pythia.run_apply, conn, "APP",
                           apply_ns(td, file="x.sql", **ns_kw), text)
            assert not executed_ddl(conn, "create or replace") and \
                not executed_ddl(conn, "drop") and not executed_ddl(conn, "grant")
            assert why in out, out
        assert pythia.read_lease(td)["writes"] == 0


def test_lint_errors_are_not_landed_by_autopilot():
    with tempfile.TemporaryDirectory() as td:
        lease(td)
        bad = ("CREATE OR REPLACE PACKAGE BODY pkg_order AS\n"
               "  PROCEDURE p IS BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END;\n"
               "END;\n/\n")
        conn = FakeConn(base_script())
        _, out = quiet(pythia.run_apply, conn, "APP",
                       apply_ns(td, file="b.sql"), bad)
        assert not executed_ddl(conn, "create or replace")
        assert "lint error" in out


def test_a_leased_group_passes_a_deny_policy_and_an_unleased_one_does_not():
    with tempfile.TemporaryDirectory() as td:
        lease(td, groups=("plsql_source", "structural"))
        conn = FakeConn(base_script())
        code, _ = quiet(pythia.run_apply, conn, "APP", apply_ns(td, file="t.sql"),
                        "create table t_new (id number constraint pk_new "
                        "primary key)\n")
        assert code == 0 and executed_ddl(conn, "create table")
        expect_exit(lambda: quiet(pythia.run_apply, FakeConn(base_script()),
                                  "APP", apply_ns(td, file="g.sql"),
                                  "grant select on t to u\n"), "deny")


def test_a_destructive_statement_under_a_lease_waits_for_its_own_approval():
    """The card says destructive statements still need the developer's
    approval each time — so the lease's deny override must let them be
    previewed and approved one by one, while never landing them itself."""
    with tempfile.TemporaryDirectory() as td:
        lease(td, groups=("structural",))           # structural is deny by policy
        conn = FakeConn(base_script())
        code, out = quiet(pythia.run_apply, conn, "APP",
                          apply_ns(td, file="d.sql"), "drop table t_old\n")
        assert code == 0 and not executed_ddl(conn, "drop")
        assert "destructive" in out and "DESTRUCTIVE" in out
        tok = last_meta(td)["token"]
        pythia.mint_grant(td, tok, "DEV")             # the developer approved it
        conn = FakeConn(base_script())
        code, _ = quiet(pythia.run_apply, conn, "APP",
                        apply_ns(td, file="d.sql", confirm=tok), "drop table t_old\n")
        assert code == 0 and executed_ddl(conn, "drop")
        assert last_meta(td)["confirmed_via"] == "grant"
        pythia.revoke_lease(td)                       # no lease: deny again
        expect_exit(lambda: quiet(pythia.run_apply, FakeConn(base_script()), "APP",
                                  apply_ns(td, file="d.sql"), "drop table t_old\n"),
                    "deny")


def test_the_lease_pauses_when_other_objects_break():
    with tempfile.TemporaryDirectory() as td:
        lease(td)
        conn = FakeConn(base_script(invalid_after=[("X_ALREADY_BROKEN", "PROCEDURE"),
                                                   ("P_USES_ORDER", "PROCEDURE")]))
        code, out = quiet(pythia.run_apply, conn, "APP",
                          apply_ns(td, file="b.sql"), p3.NEW_FILE)
        assert code == 3
        assert pythia.lease_status(pythia.read_lease(td)) == "paused"
        assert "Autopilot stopped itself" in out
        conn2 = FakeConn(base_script())
        _, out = quiet(pythia.run_apply, conn2, "APP",
                       apply_ns(td, file="b.sql"), p3.NEW_FILE)
        assert not executed_ddl(conn2, "create or replace")
        assert "paused" in out


def test_own_compile_errors_keep_the_lease_until_five_in_a_row():
    err = [("PKG_ORDER", "PACKAGE BODY", 1, 2, 3, "ERROR", "PLS-00103: x")]
    with tempfile.TemporaryDirectory() as td:
        lease(td)
        for i in range(pythia.LEASE_FAILS_TO_PAUSE):
            assert pythia.lease_status(pythia.read_lease(td)) == "active", i
            code, _ = quiet(pythia.run_apply, FakeConn(base_script(errors=err)),
                            "APP", apply_ns(td, file="b.sql"), p3.NEW_FILE)
            assert code == 3
        assert pythia.lease_status(pythia.read_lease(td)) == "paused"


def test_the_lease_ends_by_clock_budget_and_revocation():
    with tempfile.TemporaryDirectory() as td:
        lz = lease(td, max_writes=1)
        later = datetime.datetime.fromisoformat(lz["expires_at"]) + \
            datetime.timedelta(minutes=1)
        assert pythia.lease_status(lz, now=later) == "expired"
        pythia.lease_note_write(td, True, [], "e1")
        assert pythia.lease_status(pythia.read_lease(td)) == "spent"
        lease(td)
        assert pythia.revoke_lease(td)["revoked_at"]
        assert pythia.lease_status(pythia.read_lease(td)) == "revoked"
        ok, _, why = pythia.autopilot_check(td, "DEV", "plsql_source", "x")
        assert not ok and "revoked" in why


def test_autopilot_on_needs_a_console_and_request_does_not():
    import argparse
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td) / ".pythia"
        d.mkdir()
        (d / "connections.json").write_text(json.dumps(
            {"DEV": {"user": "app", "password": "x", "dsn": "h/s"}}),
            encoding="utf-8")
        old = os.getcwd()
        os.chdir(td)
        try:
            ns = argparse.Namespace(action="on", conn=None, groups=None,
                                    hours=None, max_writes=None,
                                    destructive=False, note=None, token=None,
                                    json=True, project_root=td, color=False,
                                    console=False)
            expect_exit(lambda: quiet(pythia.cmd_autopilot, None, None, ns),
                        "console")
            assert pythia.read_lease(td) is None
            ns.action = "request"
            _, out = quiet(pythia.cmd_autopilot, None, None, ns)
            tok = json.loads(out)["token"]
            assert pythia.read_lease(td) is None             # nothing delegated
            kind, _, meta = pythia.find_approvable(td, tok)
            assert kind == "autopilot"
            ns.action, ns.console = "on", True
            _, out = quiet(pythia.cmd_autopilot, None, None, ns)
            assert json.loads(out)["lease"]["approver"] == "console"
        finally:
            os.chdir(old)


def test_every_door_can_start_a_lease_and_none_grants_more():
    with tempfile.TemporaryDirectory() as td:
        req = pythia.new_lease_request(td, "DEV", {})
        _, meta, body = pythia.approval_card(td, req["token"])
        card = f"pythia approve {req['token']}\n" + "\n".join(body)
        out = p3._run_hook(td, p3._hook_payload(card, "Approve"))
        assert "autopilot ACTIVE" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
        lz = pythia.read_lease(td)
        assert lz["approver"] == "chat" and lz["session"] == "sess-1"
        # the request is spent: approving it again has nothing to approve
        expect_exit(lambda: pythia.approval_card(td, req["token"]), "no pending")
        # and the MCP door, with an elicitation answered Approve
        req2 = pythia.new_lease_request(td, "DEV", {}, groups=["plsql_source"])
        res = pythia.mcp_approve_decision(
            td, req2["token"], lambda m, s: ("accept", {"decision": "Approve"}))
        assert res["isError"] is False
        assert pythia.read_lease(td)["approver"] == "mcp"
        assert pythia.read_lease(td)["groups"] == ["plsql_source"]


def test_autopilot_report_lists_every_write_and_its_undo():
    import argparse
    with tempfile.TemporaryDirectory() as td:
        lease(td)
        quiet(pythia.run_apply, FakeConn(base_script()), "APP",
              apply_ns(td, file="b.sql"), p3.NEW_FILE)
        ns = argparse.Namespace(action="report", token=None, json=True,
                                project_root=td, color=False)
        _, out = quiet(pythia.cmd_autopilot, None, None, ns)
        rep = json.loads(out)
        assert len(rep["writes"]) == 1 and rep["writes"][0]["verdict"] == "ok"
        assert "journal restore" in rep["writes"][0]["restore"]


# --- the chat hook: fail closed, and say why ------------------------------------

def test_hook_refuses_prefilled_answers():
    with tempfile.TemporaryDirectory() as td:
        card = p3._pending_preview(td)
        payload = p3._hook_payload(card, "Approve")
        payload["tool_input"]["answers"] = {card: "Approve"}
        out = p3._run_hook(td, payload)
        assert pythia.read_grant(td, "7f3a91") is None
        assert "pre-filled" in out


def test_hook_names_an_answer_that_never_arrived():
    with tempfile.TemporaryDirectory() as td:
        card = p3._pending_preview(td)
        payload = p3._hook_payload(card, "Approve")
        payload["tool_response"] = {"answers": {}}
        out = p3._run_hook(td, payload)
        assert pythia.read_grant(td, "7f3a91") is None
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "no answer came back" in ctx and "pythia_approve" in ctx
        assert "doctor" in ctx


def test_the_mcp_tool_demands_a_prompt_on_every_call():
    tool = pythia._MCP_TOOL_DEF
    assert tool["_meta"]["anthropic/requiresUserInteraction"] is True
    assert "autopilot" in tool["description"] and "deploy" in tool["description"]


# --- deploy ------------------------------------------------------------------------

def deploy_ns(td, paths, **kw):
    ns = apply_ns(td, paths=[str(p) for p in paths], command="deploy",
                  color=False, preview=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


RELEASE = """create table t_line (id number constraint pk_line primary key);
comment on table t_line is 'Order lines';
comment on column t_line.id is 'Key';
create or replace package body pkg_order as
  procedure p is begin null; end;
end pkg_order;
/
"""


def deploy_conn():
    s = base_script()
    s["from all_col_comments"] = ([("COMMENTS",)], [])
    return FakeConn(s)


def test_deploy_previews_everything_and_writes_nothing():
    with tempfile.TemporaryDirectory() as td:
        policy(td, structural="confirm")
        f = pathlib.Path(td) / "rel.sql"
        f.write_text(RELEASE, encoding="utf-8")
        conn = deploy_conn()
        _, out = quiet(pythia.cmd_deploy, conn, "APP", deploy_ns(td, [f], json=True))
        doc = json.loads(out)
        assert doc["mode"] == "preview" and len(doc["members"]) == 4
        assert [m["group"] for m in doc["members"]] == [
            "structural", "comments", "comments", "plsql_source"]
        assert not executed_ddl(conn, "create") and not executed_ddl(conn, "comment")
        assert len(doc["batch"]) == 10
        _, _, body = pythia.approval_card(td, doc["batch"])
        assert "4 statements" in body[0]


def test_one_approval_runs_the_whole_release_in_order():
    with tempfile.TemporaryDirectory() as td:
        policy(td, structural="confirm")
        f = pathlib.Path(td) / "rel.sql"
        f.write_text(RELEASE, encoding="utf-8")
        _, out = quiet(pythia.cmd_deploy, deploy_conn(), "APP",
                       deploy_ns(td, [f], json=True))
        batch = json.loads(out)["batch"]
        _, meta, _ = pythia.approval_card(td, batch)
        pythia.grant_approval(td, batch, meta, "console")
        conn = deploy_conn()
        _, out = quiet(pythia.cmd_deploy, conn, "APP",
                       deploy_ns(td, [f], json=True, confirm=batch))
        doc = json.loads(out)
        assert doc["ok"] and doc["stopped_at"] is None
        order = [s.lstrip().lower()[:20] for s, _ in conn.executed
                 if s.lstrip().lower().startswith(("create", "comment"))]
        assert order[0].startswith("create table") and \
            order[-1].startswith("create or replace")
        # one approval, one run
        expect_exit(lambda: quiet(pythia.cmd_deploy, deploy_conn(), "APP",
                                  deploy_ns(td, [f], json=True, confirm=batch)),
                    "already ran")


def test_deploy_without_approval_is_refused():
    with tempfile.TemporaryDirectory() as td:
        policy(td, structural="confirm")
        f = pathlib.Path(td) / "rel.sql"
        f.write_text(RELEASE, encoding="utf-8")
        _, out = quiet(pythia.cmd_deploy, deploy_conn(), "APP",
                       deploy_ns(td, [f], json=True))
        batch = json.loads(out)["batch"]
        conn = deploy_conn()
        expect_exit(lambda: quiet(pythia.cmd_deploy, conn, "APP",
                                  deploy_ns(td, [f], confirm=batch)),
                    "no developer approval")
        assert not executed_ddl(conn, "create")


def test_deploy_stops_at_the_first_broken_statement():
    with tempfile.TemporaryDirectory() as td:
        policy(td, structural="confirm")
        f = pathlib.Path(td) / "rel.sql"
        f.write_text(RELEASE + "create or replace procedure p_after as begin "
                     "null; end;\n/\n", encoding="utf-8")
        lease(td, groups=("plsql_source", "comments", "structural"))
        s = base_script(errors=[("PKG_ORDER", "PACKAGE BODY", 1, 2, 3, "ERROR",
                                 "PLS-00103: x")])
        s["from all_col_comments"] = ([("COMMENTS",)], [])
        conn = FakeConn(s)
        try:
            quiet(pythia.cmd_deploy, conn, "APP", deploy_ns(td, [f], json=True))
            raise AssertionError("expected exit 3")
        except SystemExit as e:
            assert e.code == 3
        assert not [x for x, _ in conn.executed if "p_after" in x.lower()]


def test_deploy_refuses_up_front_what_the_policy_denies():
    with tempfile.TemporaryDirectory() as td:
        f = pathlib.Path(td) / "rel.sql"
        f.write_text(RELEASE, encoding="utf-8")
        conn = deploy_conn()
        expect_exit(lambda: quiet(pythia.cmd_deploy, conn, "APP",
                                  deploy_ns(td, [f])),
                    "before anything ran", "structural")
        assert conn.executed == []
        f.write_text("begin null; end;\n/\n", encoding="utf-8")
        expect_exit(lambda: quiet(pythia.cmd_deploy, conn, "APP",
                                  deploy_ns(td, [f])), "anonymous")


def test_a_directory_deploys_in_dependency_order_unless_numbered():
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        (d / "a_body.sql").write_text("create or replace package body pkg as "
                                      "end;\n/\n", encoding="utf-8")
        (d / "b_table.sql").write_text("create table t (x number);\n",
                                       encoding="utf-8")
        (d / "c_spec.sql").write_text("create or replace package pkg as end;\n/\n",
                                      encoding="utf-8")
        files, _, _ = pythia.collect_deploy([d])
        assert [f.name for f in files] == ["b_table.sql", "c_spec.sql", "a_body.sql"]
        n = pathlib.Path(td) / "n"
        n.mkdir()
        for name in ("02_body.sql", "01_table.sql"):
            (n / name).write_text("select 1 from dual;\n", encoding="utf-8")
        files, _, _ = pythia.collect_deploy([n])
        assert [f.name for f in files] == ["01_table.sql", "02_body.sql"]


# --- hooks ----------------------------------------------------------------------------

def pre(td, command, mode="default", tool="Bash", tin=None):
    return pythia.pre_tool_decision({"tool_name": tool, "permission_mode": mode,
                                     "cwd": td,
                                     "tool_input": tin or {"command": command}})


def decision(out):
    return (out or {}).get("hookSpecificOutput", {}).get("permissionDecision")


def test_pre_tool_lets_reads_through_and_nothing_chained():
    with tempfile.TemporaryDirectory() as td:
        assert decision(pre(td, "pythia ls PKG_%")) == "allow"
        assert decision(pre(td, 'python -m pythia sql "select a||b from t"')) == "allow"
        assert decision(pre(td, "python3 scripts/pythia.py impact T_ORDER")) == "allow"
        assert decision(pre(td, "pythia apply f.sql")) == "allow"     # preview
        for cmd in ("pythia ls x && rm -rf /", "pythia ls x; rm y",
                    "pythia ls $(rm -rf ~)", "pythia ls `id`",
                    "pythia ls x | sh", "pythia policy set structural allow",
                    "pythia apply f.sql --yes", "pythia conventions --init",
                    "pythia autopilot on", "echo hi"):
            assert decision(pre(td, cmd)) is None, cmd


def test_pre_tool_denies_writes_around_the_gate():
    with tempfile.TemporaryDirectory() as td:
        for cmd in ("sqlplus app/x@db <<EOF\ncreate or replace procedure p as "
                    "begin null; end;\n/\nEOF",
                    "echo 'drop table t;' | sqlplus -s app/x@db",
                    "sql app/x@db @release.sql",
                    "pythia approve --hook < fake.json"):
            assert decision(pre(td, cmd)) == "deny", cmd
        assert decision(pre(td, "sqlplus -v")) is None
        assert decision(pre(td, "echo 'select 1 from dual;' | sqlplus -s a/b@c")) is None
        assert decision(pre(td, "", tool="mcp__sqlcl__run-sql",
                            tin={"sql": "CREATE OR REPLACE PROCEDURE p AS BEGIN NULL; END;"})) == "deny"
        assert decision(pre(td, "", tool="mcp__sqlcl__run-sql",
                            tin={"sql": "select * from t_order"})) is None


def test_pre_tool_passes_approved_writes_only():
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td) / ".pythia"
        d.mkdir()
        (d / "connections.json").write_text(json.dumps({"DEV": {"user": "a"}}),
                                            encoding="utf-8")
        cmd = "pythia apply f.sql --confirm 7f3a91"
        assert decision(pre(td, cmd)) is None                # client decides
        pythia.mint_grant(td, "7f3a91", "DEV")
        assert decision(pre(td, cmd)) == "allow"
        assert decision(pre(td, cmd + " --conn TEST")) is None   # other conn
        assert decision(pre(td, cmd, mode="plan")) is None
        pythia.spend_grant(td, "7f3a91")
        assert decision(pre(td, cmd)) is None
        lease(td)
        assert decision(pre(td, "pythia apply f.sql --confirm 999999")) == "allow"
        (d / "settings.json").write_text(json.dumps({"auto_allow_reads": False}),
                                         encoding="utf-8")
        assert decision(pre(td, "pythia ls x")) is None      # the developer opted out


def test_post_tool_puts_the_verdict_next_to_the_result():
    def note(cmd, stdout):
        out = pythia.post_tool_note({"tool_name": "Bash",
                                     "tool_input": {"command": cmd},
                                     "tool_response": {"stdout": stdout}})
        return (out or {}).get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "EXIT 3" in note("pythia apply f.sql --confirm abc123",
                            "Applied P (PROCEDURE) — but it did not compile cleanly:")
    assert "pipe" in note("pythia apply f.sql | tail -5", "anything")
    assert "Refusal" in note("pythia apply f.sql", "Refused: structural is set to deny") \
        or "refusal" in note("pythia apply f.sql", "Refused: structural is set to deny")
    assert note("pythia ls x", "did not compile cleanly") == ""
    assert note("pythia apply f.sql", "Applied P. Compiled clean.") == ""


def test_session_start_prints_the_guide_and_the_live_state():
    import argparse
    with tempfile.TemporaryDirectory() as td:
        lease(td)
        d = pathlib.Path(td) / ".pythia"
        (d / "conventions.md").write_text("# rules", encoding="utf-8")
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("")
        try:
            _, out = quiet(pythia.cmd_hook, None, None,
                           argparse.Namespace(event="session-start",
                                              project_root=td))
        finally:
            sys.stdin = old_stdin
        assert "PYTHIA HARNESS ACTIVE" in out
        assert "autopilot: ACTIVE on DEV" in out
        assert "conventions.md" in out


def test_hooks_fail_silent_on_garbage():
    import argparse
    old_stdin = sys.stdin
    try:
        for event in ("pre-tool", "post-tool"):
            sys.stdin = io.StringIO("{not json")
            _, out = quiet(pythia.cmd_hook, None, None,
                           argparse.Namespace(event=event, project_root="."))
            assert out == ""
    finally:
        sys.stdin = old_stdin


def test_no_pythia_pretool_matcher_can_catch_askuserquestion():
    for entry in pythia.CLAUDE_HOOKS["PreToolUse"]:
        assert not pythia._matcher_hits(entry["matcher"], "AskUserQuestion")
    hooks = json.loads((HERE.parent / "hooks" / "hooks.json").read_text(
        encoding="utf-8"))["hooks"]
    for entry in hooks["PreToolUse"]:
        assert not pythia._matcher_hits(entry["matcher"], "AskUserQuestion")


# --- doctor -------------------------------------------------------------------------------

def test_doctor_finds_what_breaks_askuserquestion():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        cl = pathlib.Path(td) / ".claude"
        cl.mkdir()
        (cl / "settings.json").write_text(json.dumps({
            "permissions": {"allow": ["AskUserQuestion"]},
            "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
                {"type": "command", "command": "log-everything"}]},
                {"matcher": "Bash", "hooks": [
                    {"type": "command", "command": "python -m pythia hook pre-tool"}]}]}}),
            encoding="utf-8")
        sk = pathlib.Path(home) / ".claude" / "skills" / "eager"
        sk.mkdir(parents=True)
        (sk / "SKILL.md").write_text("---\nname: eager\ndescription: Use when x\n"
                                     "allowed-tools: Bash, AskUserQuestion\n---\n",
                                     encoding="utf-8")
        (pathlib.Path(home) / ".claude" / "settings.json").write_text(
            json.dumps({"permissions": {"defaultMode": "dontAsk"}}), encoding="utf-8")
        found = [w for _, w in pythia.ask_question_breakers(td, home)]
        assert any("permissions.allow" in w for w in found)
        assert any("'*'" in w for w in found)
        assert not any("hook pre-tool" in w for w in found)   # ours is Bash-only
        assert any("allowed-tools" in w for w in found)
        assert any("dontAsk" in w for w in found)


def test_doctor_reports_the_wiring_after_install():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        pythia.install_claude_mcp(td)
        pythia.install_claude_hooks(td, trust_mcp=True)
        rows = pythia.harness_checks(td, home)
        msgs = {(s, a): m for s, a, m in rows}
        assert all(s != "fail" for s, _, _ in rows), rows
        hooks = [m for (s, a), m in msgs.items() if a == "claude hooks"]
        assert all("wired" in m for m in hooks) and len(hooks) >= 1
        assert any(a == "approval door" and s == "ok" for s, a, _ in rows)


def test_install_registers_the_mcp_approver_once_and_keeps_other_servers():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / ".mcp.json"
        path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}),
                        encoding="utf-8")
        assert pythia.install_claude_mcp(td) == (path, "added")
        s = json.loads(path.read_text(encoding="utf-8"))
        assert s["mcpServers"]["other"] == {"command": "x"}
        assert s["mcpServers"]["pythia"]["args"] == ["-m", "pythia", "mcp"]
        assert pythia.install_claude_mcp(td) == (path, "present")
        path.write_text("{broken", encoding="utf-8")
        assert pythia.install_claude_mcp(td) == (path, None)
        assert path.read_text(encoding="utf-8") == "{broken"


# --- explain, skill ---------------------------------------------------------------------------

def test_explain_parses_never_runs_and_rolls_back():
    import argparse
    plan = ["Plan hash value: 1",
            "| Id | Operation          | Name    |",
            "|  0 | SELECT STATEMENT   |         |",
            "|  1 |  TABLE ACCESS FULL | T_ORDER |",
            "   - dynamic statistics used: dynamic sampling (level=2)"]
    conn = FakeConn({"dbms_xplan.display": ([("PLAN_TABLE_OUTPUT",)],
                                            [(ln,) for ln in plan])})
    ns = argparse.Namespace(statement=["select * from t_order where id = 1"],
                            file=None, format="TYPICAL", json=True)
    _, out = quiet(pythia.cmd_explain, conn, "APP", ns)
    doc = json.loads(out)
    assert doc["plan"] == plan
    assert any("T_ORDER" in n for n in doc["notes"])
    assert any("statistics" in n for n in doc["notes"])
    assert conn.executed[0][0].startswith("EXPLAIN PLAN SET STATEMENT_ID")
    assert conn.rollbacks == 1
    ns.statement = ["delete from t_order"]
    expect_exit(lambda: pythia.cmd_explain(FakeConn({}), "APP", ns), "SELECT")


def test_skill_new_scaffolds_what_skill_lint_accepts_once_filled():
    import argparse
    with tempfile.TemporaryDirectory() as td:
        ns = argparse.Namespace(action="new", name="team-release-notes",
                                paths=[], dir=None,
                                trigger=["writing", "release", "notes"],
                                project_root=td, json=False)
        quiet(pythia.cmd_skill, None, None, ns)
        path = pathlib.Path(td) / ".claude" / "skills" / "team-release-notes" / "SKILL.md"
        assert path.is_file()
        assert pythia.skill_lint_findings(path) == []
        path.write_text("---\nname: wrong\ndescription: does things: well\n---\n",
                        encoding="utf-8")
        probs = " ".join(pythia.skill_lint_findings(path))
        assert "folder" in probs and "Use when" in probs and "': '" in probs
        expect_exit(lambda: quiet(pythia.cmd_skill, None, None, ns), "exists")


def test_new_commands_are_in_the_parser_and_the_guide():
    p = pythia.build_parser()
    for argv in (["autopilot", "request", "--groups", "all", "--hours", "4"],
                 ["deploy", "a.sql", "b.sql"], ["lint", "x.sql"],
                 ["explain", "select 1 from dual"], ["stats", "T"],
                 ["comments", "T", "--scaffold"], ["doctor", "--db"],
                 ["hook", "pre-tool"], ["skill", "lint"],
                 ["apply", "f.sql", "--preview"]):
        p.parse_args(argv)
    for cmd in ("autopilot", "deploy", "lint", "explain", "stats", "comments",
                "doctor", "hook", "skill"):
        assert cmd in pythia.COMMANDS and cmd in pythia.OPERATING_GUIDE


def main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except (Exception, SystemExit) as e:  # noqa: BLE001 — keep going
                failed += 1
                print(f"FAIL {name}: {e!r}")
    if failed:
        sys.exit(f"{failed} test(s) failed")
    print("OK")


if __name__ == "__main__":
    main()
