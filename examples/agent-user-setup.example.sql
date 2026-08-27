-- The three-role layout for AI-agent development on Oracle — run as a DBA.
-- Replace APP_ADMIN / APP_OWNER / APP_AGENT and every password. Full
-- reasoning: GUIDE.md section 3. The short version:
--
--   APP_ADMIN  admin work only (users, grants, Data Pump). Owns nothing.
--   APP_OWNER  owns the schema; the developer's daily account. NEVER holds
--              DBA or RESOURCE — a proxy session inherits the owner's whole
--              power, so anything the owner holds, the agent holds.
--   APP_AGENT  the agent's credential: logon only, owns nothing, can own
--              nothing. Proxies into APP_OWNER.
--
-- Why proxy and not ANY grants: CREATE ANY PROCEDURE spans every schema on
-- the instance; on a shared instance one wrong run touches someone else's
-- code. Proxy gives full power inside exactly one schema, zero outside.
--
-- Passwords are real passwords from day one — never the username.

-- 1. The admin account (skip if your site already has one)
CREATE USER app_admin IDENTIFIED BY "ChangeMe_Admin";
GRANT DBA TO app_admin;

-- 2. The schema owner, with only what development needs
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
-- Deliberately absent: DBA, RESOURCE, any ANY privilege, ALTER SYSTEM.
-- (RESOURCE also lacks CREATE VIEW — grant privileges explicitly instead.)

-- 3. The agent's credential: logon only
CREATE USER app_agent IDENTIFIED BY "ChangeMe_Agent";
GRANT CREATE SESSION TO app_agent;

-- 4. Let the agent proxy into the owner
ALTER USER app_owner GRANT CONNECT THROUGH app_agent;

-- The agent then connects with:  user = "app_agent[app_owner]"
-- and the agent's own password. In pythia's connections.json:
--   "default": "agent_dev",
--   "agent_dev": { "user": "app_agent[app_owner]", "schema": "APP_OWNER", ... }
-- (`pythia agent-user --save` generates steps 3-4 plus the config entry.)

-- To cut the agent off later, one statement — the owner is untouched:
--   ALTER USER app_owner REVOKE CONNECT THROUGH app_agent;

-- Audit which sessions came through the proxy:
--   select sys_context('userenv','proxy_user') from dual;   -- in-session
--   V$SESSION rows for the agent show the proxy in AUTHENTICATION_TYPE.

-- Review what an account can actually do (run for each of the three):
--   select * from dba_sys_privs  where grantee = 'APP_OWNER';
--   select * from dba_role_privs where grantee = 'APP_OWNER';
