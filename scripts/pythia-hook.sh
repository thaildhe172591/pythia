#!/bin/sh
# pythia plugin hooks: start scripts/pythia.py with a Python 3 that really
# runs. A bare `python` is not one on a fresh Windows machine: it is the
# Microsoft Store alias (it prints "Python was not found" and exits), or it
# is not on PATH at all, because the python.org installer leaves "Add
# python.exe to PATH" unchecked. Either way the hooks did not start, and the
# write guard failed open without a word (issue #43).
#
# Order: the `py` launcher on Windows (installed and on PATH by default, never
# shadowed by the alias), then python3, then python. A candidate under
# WindowsApps counts only if it actually runs: a Store Python install lives
# there too. Nothing found: the guard fails closed and says why.
# POSIX sh only, and no external commands but the interpreter: this runs
# under Git Bash on Windows, and on every Bash call.

script="${0%/*}/pythia.py"

works() {
    p=$(command -v "$1" 2>/dev/null) || return 1
    case "$p" in
        *[Ww][Ii][Nn][Dd][Oo][Ww][Ss][Aa][Pp][Pp][Ss]*)
            "$1" -c "" >/dev/null 2>&1 || return 1 ;;
    esac
    return 0
}

case "${OS:-}" in
    Windows_NT) if works py; then exec py -3 "$script" "$@"; fi ;;
esac
for cand in python3 python; do
    if works "$cand"; then exec "$cand" "$script" "$@"; fi
done

printf '%s\n' >&2 \
    'pythia: no Python 3 found to run its hooks. `python` and `python3` are either' \
    'not on PATH or the Microsoft Store alias under \Microsoft\WindowsApps\, and' \
    'there is no `py` launcher. Fix: add your Python folder (for example' \
    '%LOCALAPPDATA%\Programs\Python\Python3xx\) to PATH, or reinstall Python from' \
    'python.org with the py launcher; turn off the python.exe / python3.exe App' \
    'execution aliases; then restart Claude Code. Until then pythia'\''s write guard' \
    'blocks Bash and SQLcl run-sql rather than let a write past it unchecked.'
if [ "$1 $2" = "hook pre-tool" ]; then
    exit 2
fi
exit 1
