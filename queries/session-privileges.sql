-- Purpose: the dangerous ANY privileges this session holds. Feeds the
--          one-line warning in apply previews and check — warn, never block.
-- Binds:   (none)
-- Returns: PRIVILEGE
select privilege
  from session_privs
 where privilege like '%ANY%'
 order by privilege
