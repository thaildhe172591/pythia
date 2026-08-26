-- Purpose: every INVALID object in the schema. Captured before and after a
--          change, this is what proves a fix did not break something else.
-- Binds:   :s  schema (object owner)
-- Returns: OBJECT_NAME, OBJECT_TYPE, LAST_DDL
select object_name,
       object_type,
       to_char(last_ddl_time, 'yyyy-mm-dd hh24:mi:ss') last_ddl
  from all_objects
 where owner = :s
   and status = 'INVALID'
 order by object_type, object_name
