-- Purpose: full source of a named object, every unit (spec and body), in
--          compilation order. Backs the src command.
-- Binds:   :s  schema (object owner)
--          :n  object name
-- Returns: TYPE, LINE, TEXT
select type, line, text
  from all_source
 where owner = :s
   and name = upper(:n)
 order by type, line
