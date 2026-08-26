# Query library

Every SQL statement pythia runs lives here, one statement per file. Python
loads a file and passes named binds; nothing is interpolated into SQL.

## Contract

1. **One statement per file.** No trailing semicolon.
2. **Named binds only** (`:s`, `:n`, `:depth`). Never build SQL by
   concatenation.
3. **Header comment** with three fields, exactly these labels:

   ```sql
   -- Purpose: what question this answers, and why it is worth asking
   -- Binds:   :s  schema (object owner)
   -- Returns: COLUMN, COLUMN, COLUMN
   ```

4. **Declare the binds in code.** Add the file to `QUERY_BINDS` in
   `scripts/pythia.py`. `tests/test_phase2.py` fails if a file uses a bind the
   code does not declare, or the code declares one the file does not use, or a
   file exists with no entry at all.
5. **Read-only.** These queries only read the data dictionary.

## Testing a change

```bash
python tests/test_phase2.py
```

That checks structure and the bind contract without a database. Run the command
that uses your query against a real schema before opening a PR.
