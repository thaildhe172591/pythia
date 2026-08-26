#!/usr/bin/env node
// npx pythia-plsql — the whole kit in one command:
//   1. pip install pythia-plsql   (CLI + queries + bundled skills)
//   2. pythia install             (skills via `npx skills add` with its
//                                  interactive agent picker, + .pythia/ scaffold)
// This wrapper stays dependency-free: it only finds Python and delegates.
"use strict";
const { spawnSync } = require("node:child_process");

const onWindows = process.platform === "win32";
const run = (cmd, args, stdio) =>
  spawnSync(cmd, args, { stdio, shell: onWindows });

function findPython() {
  for (const [cmd, args] of [["python3", []], ["python", []], ["py", ["-3"]]]) {
    const r = run(cmd, [...args, "--version"], "ignore");
    if (r.status === 0) return [cmd, args];
    }
  return null;
}

const py = findPython();
if (!py) {
  console.error("Python 3.9+ is required but was not found on PATH.");
  console.error("Install it from https://python.org, then rerun: npx pythia-plsql");
  process.exit(1);
}
const [cmd, base] = py;

console.log("Installing the pythia CLI (pip install pythia-plsql)...");
let r = run(cmd, [...base, "-m", "pip", "install", "--upgrade", "pythia-plsql"], "inherit");
if (r.status !== 0) process.exit(r.status ?? 1);

// `-m pythia` instead of the console script: pip user installs may put
// Scripts/ off PATH, the module never is.
r = run(cmd, [...base, "-m", "pythia", "install"], "inherit");
process.exit(r.status ?? 0);
