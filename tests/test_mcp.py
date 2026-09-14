#!/usr/bin/env python3
"""MCP approver self-checks — no database, no network, no live Codex.

Run: python tests/test_mcp.py
"""
import io
import json
import os
import pathlib
import sys
import tempfile

os.environ.setdefault("PYTHIA_CI", "1")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import pythia  # noqa: E402


# --- Task 1: transport handshake + tools/list -------------------------------

def test_initialize_echoes_protocol_and_declares_tools():
    out, done = pythia.mcp_handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "codex", "version": "0.120.0"}}},
        {})
    assert done is False
    assert len(out) == 1
    r = out[0]
    assert r["id"] == 1 and r["jsonrpc"] == "2.0"
    assert r["result"]["protocolVersion"] == "2025-06-18"     # echo client's
    assert "tools" in r["result"]["capabilities"]
    assert r["result"]["serverInfo"]["name"] == "pythia"


def test_initialized_notification_is_silent():
    out, done = pythia.mcp_handle(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}, {})
    assert out == [] and done is False


def test_tools_list_returns_only_pythia_approve():
    out, _ = pythia.mcp_handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, {})
    tools = out[0]["result"]["tools"]
    assert [t["name"] for t in tools] == ["pythia_approve"]
    schema = tools[0]["inputSchema"]
    assert schema["properties"]["token"]["type"] == "string"
    assert schema["required"] == ["token"]


def test_unknown_method_with_id_is_an_error_not_a_crash():
    out, _ = pythia.mcp_handle(
        {"jsonrpc": "2.0", "id": 3, "method": "resources/list"}, {})
    assert out[0]["error"]["code"] == -32601        # method not found
    assert out[0]["id"] == 3


# --- Task 2: the elicitation round-trip and the mint (fail-closed) ----------

def _preview(root, token="a1b2c3"):
    """A pending plsql_source preview in the journal, the way apply writes one."""
    pythia.write_journal_entry(
        root, "PACKAGE BODY", "PKG_ORDER", "old\n",
        "CREATE OR REPLACE PACKAGE BODY pkg_order AS\n new;\nEND;\n",
        {"token": token, "connection": "DEV", "schema": "APP",
         "group": "plsql_source", "applied": False})
    return token


def test_mint_only_on_accept_with_approve():
    with tempfile.TemporaryDirectory() as td:
        token = _preview(td)
        seen = {}

        def elicit(message, schema):
            seen["message"] = message
            seen["schema"] = schema
            return "accept", {"decision": "Approve"}

        res = pythia.mcp_approve_decision(td, token, elicit)
        assert res["isError"] is False
        assert token in res["content"][0]["text"]
        assert "PKG_ORDER" in seen["message"]                 # the card was shown
        assert seen["schema"]["properties"]["decision"]["enum"] == ["Approve", "Reject"]
        g = pythia.read_grant(td, token)
        assert g and g["approver"] == "mcp" and g["used_at"] is None


def test_no_mint_on_reject_decline_or_cancel():
    for action, content in (("accept", {"decision": "Reject"}),
                            ("decline", {}), ("cancel", {})):
        with tempfile.TemporaryDirectory() as td:
            token = _preview(td)
            res = pythia.mcp_approve_decision(td, token, lambda m, s: (action, content))
            assert res["isError"] is True
            assert pythia.read_grant(td, token) is None       # nothing minted


def test_no_mint_for_unknown_token_and_elicit_is_not_even_called():
    with tempfile.TemporaryDirectory() as td:
        called = []
        res = pythia.mcp_approve_decision(
            td, "nope99",
            lambda m, s: called.append(1) or ("accept", {"decision": "Approve"}))
        assert res["isError"] is True
        assert called == []                                   # refused before asking
        assert pythia.read_grant(td, "nope99") is None


def test_elicit_error_fails_closed():
    with tempfile.TemporaryDirectory() as td:
        token = _preview(td)

        def boom(message, schema):
            raise RuntimeError("transport died")

        res = pythia.mcp_approve_decision(td, token, boom)
        assert res["isError"] is True
        assert pythia.read_grant(td, token) is None


# --- Task 3: the server loop, the mcp subcommand, and the guide -------------

class ScriptedClient:
    """Feeds the server initialize -> tools/call, then answers the server's
    elicitation/create with a fixed (action, decision). Acts as both reader
    and writer for mcp_serve."""
    def __init__(self, token, action="accept", decision="Approve"):
        self.out = io.StringIO()
        self.answered = False
        self.action, self.decision = action, decision
        self._pending = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "pythia_approve", "arguments": {"token": token}}},
        ]

    def readline(self):
        if self._pending:
            return json.dumps(self._pending.pop(0)) + "\n"
        if not self.answered:
            for line in reversed(self.out.getvalue().splitlines()):
                m = json.loads(line)
                if m.get("method") == pythia.MCP_ELICIT_METHOD:
                    self.answered = True
                    return json.dumps({"jsonrpc": "2.0", "id": m["id"],
                                       "result": {"action": self.action,
                                                  "content": {"decision": self.decision}}}) + "\n"
        return ""     # EOF -> server loop ends

    def write(self, s):
        self.out.write(s)

    def flush(self):
        pass

    def messages(self):
        return [json.loads(l) for l in self.out.getvalue().splitlines() if l.strip()]


def test_serve_end_to_end_mints_on_scripted_approve():
    with tempfile.TemporaryDirectory() as td:
        token = _preview(td)
        client = ScriptedClient(token)
        pythia.mcp_serve(client, client, td)
        msgs = client.messages()
        assert any(m.get("result", {}).get("serverInfo", {}).get("name") == "pythia"
                   for m in msgs)
        assert any(m.get("method") == pythia.MCP_ELICIT_METHOD for m in msgs)
        call_result = [m for m in msgs if m.get("id") == 2 and "result" in m][-1]
        assert call_result["result"]["isError"] is False
        assert pythia.read_grant(td, token)["approver"] == "mcp"


def test_serve_fails_closed_on_scripted_decline():
    with tempfile.TemporaryDirectory() as td:
        token = _preview(td)
        client = ScriptedClient(token, action="decline", decision="")
        pythia.mcp_serve(client, client, td)
        call_result = [m for m in client.messages()
                       if m.get("id") == 2 and "result" in m][-1]
        assert call_result["result"]["isError"] is True
        assert pythia.read_grant(td, token) is None


def test_mcp_is_a_no_db_command_in_the_guide():
    assert "mcp" in pythia.COMMANDS
    assert "mcp" in pythia.NO_DB_COMMANDS
    assert "mcp" in pythia.OPERATING_GUIDE


# --- Task 4: config.toml and requirements.toml merges (TOML-safe) -----------

def test_merge_codex_mcp_config_appends_once_when_clean():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / ".codex" / "config.toml"
        p, action = pythia.merge_codex_mcp_config(path)
        assert p == path and action == "created"
        body = path.read_text(encoding="utf-8")
        assert "[mcp_servers.pythia]" in body
        assert 'args = ["-m", "pythia", "mcp"]' in body
        assert "mcp_elicitations = true" in body
        assert pythia.merge_codex_mcp_config(path)[1] == "present"   # idempotent


def test_merge_codex_mcp_config_preserves_other_content():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / ".codex" / "config.toml"
        path.parent.mkdir(parents=True)
        path.write_text('model = "gpt-5"\n', encoding="utf-8")
        _, action = pythia.merge_codex_mcp_config(path)
        assert action == "appended"
        body = path.read_text(encoding="utf-8")
        assert body.startswith('model = "gpt-5"\n')                  # kept
        assert "[mcp_servers.pythia]" in body


def test_merge_codex_mcp_config_refuses_on_table_collision():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / ".codex" / "config.toml"
        path.parent.mkdir(parents=True)
        original = "[approval_policy.granular]\nmcp_elicitations = false\n"
        path.write_text(original, encoding="utf-8")
        p, action = pythia.merge_codex_mcp_config(path)
        assert action == "conflict"
        assert path.read_text(encoding="utf-8") == original          # untouched


def test_merge_codex_requirements_creates_and_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / ".codex" / "requirements.toml"
        p, action = pythia.merge_codex_requirements(path)
        assert action == "created"
        body = path.read_text(encoding="utf-8")
        assert "allowed_approval_policies" in body and "allowed_sandbox_modes" in body
        assert "danger-full-access" not in body     # the forbidden mode is absent
        assert '"never"' not in body                 # the forbidden policy is absent
        assert pythia.merge_codex_requirements(path)[1] == "present"


def test_merge_codex_requirements_refuses_an_existing_file():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / ".codex" / "requirements.toml"
        path.parent.mkdir(parents=True)
        original = "# org policy\nallowed_sandbox_modes = []\n"
        path.write_text(original, encoding="utf-8")
        p, action = pythia.merge_codex_requirements(path)
        assert action == "conflict"
        assert path.read_text(encoding="utf-8") == original          # untouched


# --- Task 5: wire the MCP install into cmd_install (project scope) ----------

def test_cmd_install_registers_the_mcp_server_and_pins_the_sandbox():
    import argparse
    import contextlib
    old = os.environ.get("PATH")
    os.environ["PATH"] = ""
    try:
        with tempfile.TemporaryDirectory() as td:
            ns = argparse.Namespace(project_root=td, glob=False, source=None,
                                    color=False, json=False, no_hooks=False)
            with contextlib.redirect_stdout(io.StringIO()):
                pythia.cmd_install(None, None, ns)
            cfg = pathlib.Path(td) / ".codex" / "config.toml"
            req = pathlib.Path(td) / ".codex" / "requirements.toml"
            assert "[mcp_servers.pythia]" in cfg.read_text(encoding="utf-8")
            assert "mcp_elicitations = true" in cfg.read_text(encoding="utf-8")
            assert "allowed_sandbox_modes" in req.read_text(encoding="utf-8")
    finally:
        if old is not None:
            os.environ["PATH"] = old


def test_cmd_install_no_hooks_skips_the_mcp_files():
    import argparse
    import contextlib
    old = os.environ.get("PATH")
    os.environ["PATH"] = ""
    try:
        with tempfile.TemporaryDirectory() as td:
            ns = argparse.Namespace(project_root=td, glob=False, source=None,
                                    color=False, json=False, no_hooks=True)
            with contextlib.redirect_stdout(io.StringIO()):
                pythia.cmd_install(None, None, ns)
            assert not (pathlib.Path(td) / ".codex" / "config.toml").exists()
    finally:
        if old is not None:
            os.environ["PATH"] = old


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
