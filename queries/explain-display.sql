-- Purpose: the plan EXPLAIN PLAN just wrote, formatted by DBMS_XPLAN.
--          License-safe: PLAN_TABLE only, no V$ views, no AWR/ASH.
-- Binds:   :id   statement id the EXPLAIN PLAN was tagged with
--          :fmt  DBMS_XPLAN format (BASIC, TYPICAL, ALL)
-- Returns: PLAN_TABLE_OUTPUT
select plan_table_output
  from table(dbms_xplan.display('PLAN_TABLE', :id, :fmt))
