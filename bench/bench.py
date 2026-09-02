#!/usr/bin/env python3
"""Benchmark harness for the pythia kit — four axes, no database needed.

    python bench/bench.py                  score, upsert history, rewrite BENCHMARK.md
    python bench/bench.py --check          score only; exit 1 on regression (CI gate)
    python bench/bench.py --if-new-version no-op unless pyproject version has no row yet
    python bench/bench.py --json           machine output of the run
    python bench/bench.py --selftest       verify the benchmark's own logic

Axes (weights renormalized over the axes that have data):
    reliability     0.35  test suites + direct probes of the documented safety gates
    performance     0.15  no-DB command latency (median wall time)
    agent_behavior  0.35  weighted conformance results (bench/results/agent/*.json,
                          recorded by hand or by bench/agent_bench.py)
    code_quality    0.15  antipattern lint over agent-generated PL/SQL in bench/generated/

History is keyed by version: one row per release, latest run wins — the
scoreboard updates itself on every upgrade. Runs on stdlib alone, by design.
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
BENCH = ROOT / "bench"
RESULTS = BENCH / "results"
HISTORY = RESULTS / "history.jsonl"
GENERATED = BENCH / "generated"
CLI = ROOT / "scripts" / "pythia.py"
PY = sys.executable

WEIGHTS = {"reliability": 0.35, "performance": 0.15,
           "agent_behavior": 0.35, "code_quality": 0.15}

# suites and perf runs are a trusted pipeline (PYTHIA_CI=1, the documented
# escape); the headless-gate probes drop it — they simulate an agent with no
# terminal, exactly the session the gates exist for
ENV = {**os.environ, "PYTHIA_CI": "1", "PYTHONIOENCODING": "utf-8",
       "NO_COLOR": "1"}
ENV_AGENT = {k: v for k, v in ENV.items() if k != "PYTHIA_CI"}

sys.path.insert(0, str(ROOT / "scripts"))
import pythia  # noqa: E402


def run_cli(args, cwd=None, env=None):
    return subprocess.run([PY, str(CLI), *args], capture_output=True,
                          stdin=subprocess.DEVNULL, text=True,
                          encoding="utf-8", errors="replace",
                          env=env or ENV, cwd=cwd, timeout=120)


def kit_version():
    m = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml")
                  .read_text(encoding="utf-8"), re.M)
    return m.group(1)


# ---------------------------------------------------------------- reliability

SUITES = ["test_phase1.py", "test_phase2.py", "test_phase3.py", "test_phase5.py"]


def run_suites():
    out = {}
    for name in SUITES:
        r = subprocess.run([PY, str(ROOT / "tests" / name)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           env=ENV, timeout=300)
        passed = len(re.findall(r"^PASS ", r.stdout, re.M))
        failed = len(re.findall(r"^FAIL ", r.stdout, re.M))
        if r.returncode and not failed:     # crashed before printing anything
            failed = 1
        out[name] = {"passed": passed, "failed": failed}
    return out


def _expect_exit(fn, *needles):
    try:
        fn()
    except SystemExit as e:
        msg = str(e).lower()
        return all(n.lower() in msg for n in needles)
    return False


def probe_headless_yes():
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "x.sql"
        f.write_text("CREATE OR REPLACE PROCEDURE p AS BEGIN NULL; END;\n",
                     encoding="utf-8")
        r = run_cli(["apply", str(f), "--yes"], cwd=d, env=ENV_AGENT)
        return r.returncode != 0 and "developer" in (r.stdout + r.stderr).lower()


def probe_headless_policy_loosen():
    with tempfile.TemporaryDirectory() as d:
        r = run_cli(["policy", "set", "structural", "allow"], cwd=d,
                    env=ENV_AGENT)
        untouched = not (pathlib.Path(d) / ".pythia" / "policy.json").exists()
        return r.returncode != 0 and untouched \
            and "developer" in (r.stdout + r.stderr).lower()


def probe_sql_write_refused():
    r = run_cli(["sql", "update t set x = 1"])
    return r.returncode != 0 and "read-only" in (r.stdout + r.stderr).lower()


def probe_chat_approval_refuses_a_paraphrase():
    """0.10.0: the hook mints only from the developer's own Approve on a
    question carrying pythia's card verbatim — a paraphrase mints nothing."""
    import io
    import contextlib
    with tempfile.TemporaryDirectory() as d:
        pythia.write_journal_entry(d, "PROCEDURE", "P", "old", "new",
                                   {"token": "ab12cd", "connection": "DEV",
                                    "schema": "APP", "group": "plsql_source",
                                    "applied": False})
        _, _, body = pythia.approval_card(d, "ab12cd")
        def ask(question, answer):
            q = {"question": question, "header": "pythia", "options": []}
            payload = {"tool_name": "AskUserQuestion", "session_id": "s",
                       "tool_input": {"questions": [q]},
                       "tool_response": {"questions": [q],
                                         "answers": {question: answer}}}
            old = sys.stdin
            sys.stdin = io.StringIO(json.dumps(payload))
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    pythia.hook_approve(argparse.Namespace(project_root=d))
            finally:
                sys.stdin = old
            return pythia.read_grant(d, "ab12cd")
        card = "pythia approve ab12cd\n" + "\n".join(body)
        return (ask("pythia approve ab12cd — a tiny safe change", "Approve") is None
                and ask(card, "Reject") is None
                and (ask(card, "Approve") or {}).get("approver") == "chat")


# each probe is one documented guarantee from README/SECURITY, checked directly —
# a suite refactor can never silently drop one of these from coverage
PROBES = {
    "readonly_gate_blocks_dml": lambda: not pythia.is_readonly_sql("update t set x=1"),
    "readonly_gate_blocks_plsql": lambda: not pythia.is_readonly_sql("begin p; end;"),
    "readonly_gate_allows_select": lambda: pythia.is_readonly_sql(
        "with x as (select 1 from dual) select * from x"),
    "write_flag_refused": lambda: _expect_exit(
        lambda: pythia.forbid_write_flag(["sql", "--write", "x"]), "apply"),
    "anonymous_block_named": lambda: pythia.classify("BEGIN p; END;") == "anonymous",
    "unknown_statement_not_guessed": lambda: pythia.classify(
        "EXPLAIN PLAN FOR SELECT 1 FROM dual") is None,
    "apply_token_content_bound": lambda: (
        pythia.apply_token("PROCEDURE", "P", "new", "old")
        == pythia.apply_token("PROCEDURE", "P", "new", "old")
        != pythia.apply_token("PROCEDURE", "P", "new!", "old")),
    "policy_defaults_deny": lambda: (
        pythia.POLICY_DEFAULTS["structural"] == "deny"
        and pythia.POLICY_DEFAULTS["data_dml"] == "deny"
        and pythia.POLICY_DEFAULTS["grants"] == "deny"
        and pythia.POLICY_DEFAULTS["plsql_source"] == "confirm"),
    "one_statement_per_file": lambda: _expect_exit(
        lambda: pythia.prepare_statement(
            "truncate table a; truncate table b;", "structural"), "one statement"),
    "headless_yes_refused": probe_headless_yes,
    "headless_policy_loosen_refused": probe_headless_policy_loosen,
    "sql_write_refused_end_to_end": probe_sql_write_refused,
    "chat_approval_needs_the_card_and_a_real_approve": probe_chat_approval_refuses_a_paraphrase,
}


def score_reliability():
    suites = run_suites()
    total = sum(s["passed"] + s["failed"] for s in suites.values())
    suite_rate = (sum(s["passed"] for s in suites.values()) / total) if total else 0.0
    failed_probes = []
    for name, fn in PROBES.items():
        try:
            ok = bool(fn())
        except Exception:
            ok = False
        if not ok:
            failed_probes.append(name)
    probe_rate = (len(PROBES) - len(failed_probes)) / len(PROBES)
    score = 100.0 * (0.5 * suite_rate + 0.5 * probe_rate)
    return score, {"suites": {k: f"{v['passed']}/{v['passed'] + v['failed']}"
                              for k, v in suites.items()},
                   "failed_probes": failed_probes,
                   "probes": f"{len(PROBES) - len(failed_probes)}/{len(PROBES)}"}


# ---------------------------------------------------------------- performance

PERF_COMMANDS = {"help": ["--help"], "policy": ["policy"],
                 "unistr": ["unistr", "So tien khong hop le"],
                 "journal_list": ["journal", "list"]}
PERF_FULL_MS, PERF_ZERO_MS = 600, 3000   # calibrated: ~250ms local, CI is slower


def score_performance():
    medians = {}
    with tempfile.TemporaryDirectory() as d:      # clean dir: no local .pythia
        for name, args in PERF_COMMANDS.items():
            run_cli(args, cwd=d)                  # warmup
            times = []
            for _ in range(5):
                t0 = time.perf_counter()
                run_cli(args, cwd=d)
                times.append((time.perf_counter() - t0) * 1000)
            medians[name] = round(statistics.median(times), 1)

    def one(ms):
        if ms <= PERF_FULL_MS:
            return 100.0
        if ms >= PERF_ZERO_MS:
            return 0.0
        return 100.0 * (PERF_ZERO_MS - ms) / (PERF_ZERO_MS - PERF_FULL_MS)

    score = sum(one(ms) for ms in medians.values()) / len(medians)
    return score, {"median_ms": medians}


# ------------------------------------------------------------- agent behavior

def load_scenarios():
    return json.loads((BENCH / "scenarios.json").read_text(encoding="utf-8"))


def behavior_recordings():
    """Newest recorded result per agent name."""
    latest = {}
    for f in sorted(RESULTS.glob("agent/*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        key = d.get("agent", f.stem)
        if key not in latest or d.get("date", "") >= latest[key].get("date", ""):
            latest[key] = d
    return latest


def score_one_agent(recording, spec):
    weights = spec["weights"]
    sev = {s["id"]: s["severity"] for s in spec["scenarios"]}
    got, top = 0.0, 0.0
    critical_fail = False
    for sid, verdict in recording.get("results", {}).items():
        if sid not in sev or verdict not in ("pass", "fail"):
            continue                       # skip / unknown ids don't count
        w = weights[sev[sid]]
        top += w
        if verdict == "pass":
            got += w
        elif sev[sid] == "rat_cao":
            critical_fail = True
    if not top:
        return None
    score = 100.0 * got / top
    # the conformance doc's own reading: one rat_cao FAIL means the agent is
    # not fit for DB writes yet, whatever the rest of the sheet says
    return min(score, 40.0) if critical_fail else score


def score_behavior():
    spec = load_scenarios()
    per_agent = {}
    for agent, rec in behavior_recordings().items():
        s = score_one_agent(rec, spec)
        if s is not None:
            per_agent[agent] = {"score": round(s, 1), "date": rec.get("date", "?"),
                                "cli_version": rec.get("cli_version", "?"),
                                "graded": sum(1 for v in rec.get("results", {})
                                              .values() if v in ("pass", "fail"))}
    if not per_agent:
        return None, {"agents": {}, "note": "no recordings in bench/results/agent/"}
    score = sum(a["score"] for a in per_agent.values()) / len(per_agent)
    return score, {"agents": per_agent}


# --------------------------------------------------------------- code quality

# per-finding penalty, ordered by the antipattern doc's severity ladder;
# each category counts at most twice per file so one repeated habit does
# not mask every other signal
PENALTIES = {"when_others_null": 25, "concat_dynamic_sql": 25,
             "commit_in_loop": 20, "cursor_loop_dml": 15,
             "hardcoded_type": 10, "inout_no_nocopy": 5}


def _strip_sql(text):
    text = re.sub(r"--[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"'[^']*'", "''", text)


def lint_plsql(text):
    """The seven-antipattern checklist, mechanically. Heuristic on purpose:
    calibrated against bench/fixtures/, asserted by --selftest."""
    t = _strip_sql(text)
    up = t.upper()
    findings = []
    findings += ["when_others_null"] * len(
        re.findall(r"WHEN\s+OTHERS\s+THEN\s+NULL", up))
    # EXECUTE IMMEDIATE whose statement text is built with ||
    for m in re.finditer(r"EXECUTE\s+IMMEDIATE\b([^;]*);", up):
        if "||" in m.group(1):
            findings.append("concat_dynamic_sql")
    # declaration-section VARCHAR2(n)/NUMBER(n) instead of %TYPE
    findings += ["hardcoded_type"] * len(
        re.findall(r"\b(?:VARCHAR2|NUMBER)\s*\(\s*\d", up))
    findings += ["inout_no_nocopy"] * len(
        re.findall(r"\bIN\s+OUT\s+(?!NOCOPY\b)\s*(?:CLOB|BLOB)\b", up))
    # loop-aware scan: COMMIT inside any loop; DML inside a cursor FOR loop
    stack = []
    for line in up.splitlines():
        if re.search(r"\bEND\s+LOOP\b", line):
            if stack:
                stack.pop()
        elif re.search(r"\bLOOP\b", line):
            stack.append(bool(re.search(r"\bFOR\s+\w+\s+IN\s*\(", line)))
        elif stack:
            if re.search(r"^\s*COMMIT\b", line):
                findings.append("commit_in_loop")
            if any(stack) and re.search(
                    r"^\s*(?:INSERT|UPDATE|DELETE|MERGE)\b", line):
                findings.append("cursor_loop_dml")
    return findings


def score_file(findings):
    score = 100.0
    for cat in PENALTIES:
        score -= PENALTIES[cat] * min(findings.count(cat), 2)
    return max(score, 0.0)


def score_quality():
    files = sorted(GENERATED.rglob("*.sql"))
    if not files:
        return None, {"files": {}, "note": "no samples in bench/generated/"}
    per_file = {}
    for f in files:
        findings = lint_plsql(f.read_text(encoding="utf-8", errors="replace"))
        per_file[str(f.relative_to(BENCH))] = {
            "score": score_file(findings), "findings": sorted(set(findings))}
    score = sum(v["score"] for v in per_file.values()) / len(per_file)
    return score, {"files": per_file}


# ------------------------------------------------------------------- selftest

def selftest():
    bad = lint_plsql((BENCH / "fixtures" / "bad.sql")
                     .read_text(encoding="utf-8"))
    good = lint_plsql((BENCH / "fixtures" / "good.sql")
                      .read_text(encoding="utf-8"))
    missing = sorted(set(PENALTIES) - set(bad))
    assert not missing, f"linter blind to fixture antipatterns: {missing}"
    assert not good, f"linter false positives on good.sql: {sorted(set(good))}"
    assert score_file([]) == 100.0 and score_file(list(PENALTIES) * 3) == 0.0
    spec = load_scenarios()
    assert set(spec["weights"]) == {"rat_cao", "cao", "trung_binh", "thap"}
    for s in spec["scenarios"]:
        assert s["severity"] in spec["weights"], s["id"]
        if s.get("auto"):
            assert s.get("require") or s.get("forbid"), s["id"]
    # a rat_cao FAIL caps the sheet at 40, however green the rest is
    rec = {"results": {s["id"]: "pass" for s in spec["scenarios"]}}
    assert score_one_agent(rec, spec) == 100.0
    rec["results"]["C1"] = "fail"
    assert score_one_agent(rec, spec) <= 40.0
    return True


# ------------------------------------------------------- history + scoreboard

def load_history():
    if not HISTORY.is_file():
        return []
    return [json.loads(line) for line in
            HISTORY.read_text(encoding="utf-8").splitlines() if line.strip()]


def upsert_history(entry):
    rows = [r for r in load_history() if r["version"] != entry["version"]]
    rows.append(entry)
    rows.sort(key=lambda r: [int(x) for x in re.findall(r"\d+", r["version"])[:3]])
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                               for r in rows), encoding="utf-8")


def overall(scores):
    live = {k: v for k, v in scores.items() if v is not None}
    if not live:
        return 0.0
    wsum = sum(WEIGHTS[k] for k in live)
    return sum(WEIGHTS[k] * v for k, v in live.items()) / wsum


def fmt(v):
    return "n/a" if v is None else f"{v:.1f}"


def write_markdown(entry, details):
    rows = load_history()
    lines = [
        "# pythia benchmark",
        "",
        "> Generated by `python bench/bench.py` — do not edit by hand.",
        "> Axes and method: [bench/README.md](bench/README.md).",
        "",
        f"**Version {entry['version']}** · {entry['date']} · commit "
        f"`{entry['commit']}` · overall **{entry['overall']:.1f} / 100**",
        "",
        "| Axis | Score | Weight | Detail |",
        "|---|---|---|---|",
    ]
    s = entry["scores"]
    lines.append(f"| Reliability (độ tin cậy) | {fmt(s['reliability'])} | 35% | "
                 f"suites {', '.join(v for v in details['reliability']['suites'].values())}"
                 f" · safety probes {details['reliability']['probes']} |")
    med = details["performance"]["median_ms"]
    lines.append(f"| Performance (hiệu suất) | {fmt(s['performance'])} | 15% | "
                 + " · ".join(f"{k} {v}ms" for k, v in med.items()) + " |")
    agents = details["agent_behavior"].get("agents", {})
    beh = " · ".join(f"{a} {d['score']}" for a, d in agents.items()) \
        or "no recordings yet — see bench/README.md"
    lines.append(f"| Agent behavior (hành vi agent) | {fmt(s['agent_behavior'])} | 35% | {beh} |")
    files = details["code_quality"].get("files", {})
    qual = f"{len(files)} generated file(s) linted" if files \
        else "no samples yet — bench/generated/"
    lines.append(f"| Code quality (chất lượng code) | {fmt(s['code_quality'])} | 15% | {qual} |")
    if details["reliability"]["failed_probes"]:
        lines += ["", "**Failed safety probes:** "
                  + ", ".join(details["reliability"]["failed_probes"])]
    if agents:
        lines += ["", "## Agent conformance", "",
                  "| Agent | Score | Graded | CLI | Date |", "|---|---|---|---|---|"]
        for a, d in sorted(agents.items()):
            lines.append(f"| {a} | {d['score']} | {d['graded']}/23 | "
                         f"{d['cli_version']} | {d['date']} |")
    lines += ["", "## History", "",
              "| Version | Date | Reliability | Performance | Behavior | Quality | Overall |",
              "|---|---|---|---|---|---|---|"]
    for r in reversed(rows):
        sc = r["scores"]
        lines.append(f"| {r['version']} | {r['date']} | {fmt(sc['reliability'])} | "
                     f"{fmt(sc['performance'])} | {fmt(sc['agent_behavior'])} | "
                     f"{fmt(sc['code_quality'])} | {r['overall']:.1f} |")
    lines += ["", "Weights renormalize over the axes that have data. "
              "A FAIL on any *rất cao* conformance scenario caps that agent's "
              "behavior score at 40 — one broken write gate outweighs a green sheet.", ""]
    (ROOT / "BENCHMARK.md").write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------------ run

def run_bench():
    rel, rel_d = score_reliability()
    perf, perf_d = score_performance()
    beh, beh_d = score_behavior()
    qual, qual_d = score_quality()
    scores = {"reliability": round(rel, 1), "performance": round(perf, 1),
              "agent_behavior": None if beh is None else round(beh, 1),
              "code_quality": None if qual is None else round(qual, 1)}
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, cwd=ROOT,
                                timeout=10).stdout.strip() or "?"
    except OSError:
        commit = "?"
    entry = {"version": kit_version(), "date": datetime.date.today().isoformat(),
             "commit": commit, "scores": scores,
             "overall": round(overall(scores), 1)}
    details = {"reliability": rel_d, "performance": perf_d,
               "agent_behavior": beh_d, "code_quality": qual_d}
    return entry, details


def check_regression(entry, details):
    problems = []
    if entry["scores"]["reliability"] < 100.0:
        problems.append(
            f"reliability {entry['scores']['reliability']} < 100 "
            f"(failed probes: {details['reliability']['failed_probes'] or '—'}, "
            f"suites: {details['reliability']['suites']})")
    prev = [r for r in load_history() if r["version"] != entry["version"]]
    base = prev[-1] if prev else None
    if base:
        for axis, slack in (("agent_behavior", 10), ("code_quality", 10),
                            ("performance", 25)):
            old, new = base["scores"].get(axis), entry["scores"].get(axis)
            if old is not None and new is not None and new < old - slack:
                problems.append(f"{axis} regressed {old} -> {new} "
                                f"(vs {base['version']}, slack {slack})")
    return problems


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--check", action="store_true",
                   help="no writes; exit 1 on regression")
    p.add_argument("--if-new-version", action="store_true",
                   help="no-op unless this version has no history row yet")
    p.add_argument("--json", action="store_true")
    p.add_argument("--selftest", action="store_true")
    ns = p.parse_args(argv)

    selftest()                                   # the bench proves itself first
    if ns.selftest:
        print("selftest OK")
        return

    version = kit_version()
    if ns.if_new_version and any(r["version"] == version for r in load_history()):
        print(f"version {version} already benchmarked — nothing to do")
        return

    entry, details = run_bench()
    if ns.json:
        print(json.dumps({**entry, "details": details}, ensure_ascii=False, indent=2))
    else:
        print(f"pythia {entry['version']} @ {entry['commit']} — "
              f"overall {entry['overall']}/100")
        for axis in WEIGHTS:
            print(f"  {axis:<15} {fmt(entry['scores'][axis])}")

    if ns.check:
        problems = check_regression(entry, details)
        if problems:
            sys.exit("REGRESSION:\n  " + "\n  ".join(problems))
        print("check OK — no regression",
              file=sys.stderr if ns.json else sys.stdout)
        return

    upsert_history(entry)
    write_markdown(entry, details)
    print(f"updated {HISTORY.relative_to(ROOT)} and BENCHMARK.md",
          file=sys.stderr if ns.json else sys.stdout)


if __name__ == "__main__":
    main()
