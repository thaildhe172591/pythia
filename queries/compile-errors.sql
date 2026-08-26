-- Purpose: compilation errors and warnings with the exact line and column
--          Oracle reports, so a fix can be aimed rather than guessed. This
--          closes the compile-read-fix loop.
-- Binds:   :s  schema (object owner)
--          :n  object name, or NULL for every object in the schema
-- Returns: NAME, TYPE, SEQUENCE, LINE, POSITION, ATTRIBUTE, TEXT
select name,
       type,
       sequence,
       line,
       position,
       attribute,
       text
  from all_errors
 where owner = :s
   and (:n is null or name = upper(:n))
 order by name, sequence
