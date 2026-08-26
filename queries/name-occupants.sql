-- Purpose: which object types currently hold a name in the schema's main
--          namespace. CREATE OR REPLACE cannot change an object's type, so a
--          name held by a different type must be refused at preview time —
--          otherwise the preview promises what the database will reject with
--          ORA-00955. Found by an agent during the first field test.
-- Binds:   :s  schema (object owner)
--          :n  object name
-- Returns: OBJECT_TYPE
select object_type
  from all_objects
 where owner = :s
   and object_name = upper(:n)
   and object_type in ('PROCEDURE', 'FUNCTION', 'PACKAGE', 'PACKAGE BODY',
                       'TYPE', 'TYPE BODY', 'VIEW', 'TABLE',
                       'MATERIALIZED VIEW', 'SEQUENCE', 'SYNONYM')
