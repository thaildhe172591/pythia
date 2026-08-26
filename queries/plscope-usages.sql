-- Purpose: every place an identifier is declared, referenced, assigned or
--          called. PL/Scope records this at compile time, so it is exact where
--          grep can only guess. Declarations sort first.
-- Binds:   :s  schema (object owner)
--          :n  identifier name
-- Returns: USAGE, OBJECT_NAME, OBJECT_TYPE, TYPE, LINE, COL, USAGE_ID
select usage,
       object_name,
       object_type,
       type,
       line,
       col,
       usage_id
  from all_identifiers
 where owner = :s
   and name = upper(:n)
 order by case usage when 'DECLARATION' then 0 else 1 end,
          object_name, line, col
