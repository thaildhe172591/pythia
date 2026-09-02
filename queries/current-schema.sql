-- Purpose: the schema an unqualified CREATE lands in for this session. A proxy
--          or a mis-set connections.json can put it somewhere other than the
--          schema pythia snapshots and verifies; apply refuses that mismatch.
-- Binds:   none
-- Returns: CURRENT_SCHEMA
select sys_context('userenv', 'current_schema') current_schema from dual
