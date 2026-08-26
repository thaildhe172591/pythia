<p align="center">
  <img src="https://raw.githubusercontent.com/thaildhe172591/pythia/main/assets/logo.png" alt="pythia" width="280" />
</p>

# pythia-plsql

> Oracle's MCP gives your agent a connection. **pythia gives it the judgment to use it.**

One-command installer for [pythia](https://github.com/thaildhe172591/pythia) —
PL/SQL development on Oracle Database for AI coding agents (Claude Code,
Codex, Cursor and 70+ more):

```bash
npx pythia-plsql
```

It finds Python, installs the [`pythia-plsql`](https://pypi.org/project/pythia-plsql/)
package from PyPI (CLI + expert data-dictionary queries + the seven-skill
pack), installs the skills into your agents via `npx skills add`, and
scaffolds `.pythia/connections.json`. Without Node the pip package alone
still carries the whole kit.

What the kit does: explore schemas too big to dump, measure blast radius
**before** touching anything, and land changes through a snapshot-verified
write path that never lies about rollback
(exit codes: `0` clean · `1` refused · `3` written-but-broken).

Docs, security model and the honest-rollback table:
**https://github.com/thaildhe172591/pythia**
