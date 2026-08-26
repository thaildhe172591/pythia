-- Purpose: one-row probe telling an empty result apart from a schema that was
--          never compiled with PL/Scope — the difference between "not found"
--          and "cannot know".
-- Binds:   :s  schema (object owner)
-- Returns: ENABLED
select 1 enabled
  from all_identifiers
 where owner = :s
   and rownum = 1
