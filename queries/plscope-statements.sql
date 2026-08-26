-- Purpose: the SQL statements that touch a table, and where they sit. Answers
--          "which program writes to this table?" exactly. Needs PL/Scope with
--          STATEMENTS:ALL (Oracle 12.2+).
-- Binds:   :s  schema (object owner)
--          :n  table name
-- Returns: SQL_TYPE, OBJECT_NAME, OBJECT_TYPE, LINE, COL
select s.type sql_type,
       s.object_name,
       s.object_type,
       s.line,
       s.col
  from all_statements s
  join all_identifiers i
    on i.owner            = s.owner
   and i.object_name      = s.object_name
   and i.object_type      = s.object_type
   and i.usage_context_id = s.usage_id
 where s.owner = :s
   and i.name  = upper(:n)
   and i.type  = 'TABLE'
 order by s.object_name, s.line
