-- Purpose: source of one specific unit — the snapshot read that runs before
--          every write. PACKAGE and PACKAGE BODY are distinct objects, so the
--          type is part of the identity, not a filter convenience.
-- Binds:   :s  schema (object owner)
--          :n  object name
--          :t  object type exactly as in ALL_SOURCE (e.g. PACKAGE BODY)
-- Returns: TEXT
select text
  from all_source
 where owner = :s
   and name = upper(:n)
   and type = upper(:t)
 order by line
