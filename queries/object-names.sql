-- Purpose: every object name in the schema with its type, for measuring how
--          well a naming pattern describes what is actually there. Derived
--          patterns are guesses until the schema agrees with them.
-- Binds:   :s  schema (object owner)
-- Returns: OBJECT_TYPE, OBJECT_NAME
select object_type,
       object_name
  from all_objects
 where owner = :s
   and object_type not in ('INDEX', 'LOB', 'TABLE PARTITION', 'INDEX PARTITION')
 order by object_type, object_name
