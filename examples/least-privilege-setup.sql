-- Least-privilege setup for an AI agent working on one development schema.
-- Run as a DBA-capable user. Replace APP_OWNER, APP_AGENT and the passwords.
--
-- The design is proxy authentication, because Oracle least-privilege for
-- PL/SQL development has no clean per-object form: compiling into another
-- schema needs CREATE ANY PROCEDURE (too broad), and the ALL_* views only
-- show what the session has rights on. A proxy session authenticates with
-- the agent's own credential but runs as the schema owner, which gives:
--
--   * blast radius limited to the one development schema
--   * the agent never learns the owner's password
--   * revocation is one statement, without touching the owner account
--   * the audit trail still shows who really connected (PROXY_USER)
--
-- The session still holds full power INSIDE that schema — contain the risk
-- by pointing it at a dedicated development schema, never production.

-- 1. The development schema owner, with only what development needs.
--    (Skip if the schema already exists; then just review its grants.)
CREATE USER app_owner IDENTIFIED BY "ChangeMe_Owner"
  QUOTA UNLIMITED ON users;
GRANT CREATE SESSION,
      CREATE TABLE,
      CREATE VIEW,
      CREATE SEQUENCE,
      CREATE PROCEDURE,   -- also covers functions and packages
      CREATE TRIGGER,
      CREATE TYPE,
      CREATE SYNONYM
  TO app_owner;
-- Deliberately absent: any ANY privilege, DROP USER, ALTER SYSTEM, DBA.
-- Note: the RESOURCE role lacks CREATE VIEW — grant privileges explicitly.

-- 2. The agent's credential: logon only, owns nothing, can own nothing.
CREATE USER app_agent IDENTIFIED BY "ChangeMe_Agent";
GRANT CREATE SESSION TO app_agent;

-- 3. Let the agent proxy into the schema owner.
ALTER USER app_owner GRANT CONNECT THROUGH app_agent;

-- The agent then connects with:  user = "app_agent[app_owner]"
-- and the agent's own password. In pythia's connections.json:
--   "dev": { "user": "app_agent[app_owner]", "password": "...",
--            "schema": "APP_OWNER", ... }

-- To cut the agent off later, one statement — the owner is untouched:
--   ALTER USER app_owner REVOKE CONNECT THROUGH app_agent;

-- Audit which sessions came through the proxy:
--   select sys_context('userenv','proxy_user') from dual;   -- in-session
--   V$SESSION rows for the agent show the proxy in AUTHENTICATION_TYPE.

-- Review what a schema can actually do (run for APP_OWNER and APP_AGENT):
--   select * from dba_sys_privs where grantee = 'APP_OWNER';
--   select * from dba_role_privs where grantee = 'APP_OWNER';
