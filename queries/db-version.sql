-- Purpose: which Oracle release this is — the features a script may use
--          (FETCH FIRST, identity columns, JSON) depend on it. Readable by
--          any session, unlike V$VERSION.
-- Binds:   (none)
-- Returns: PRODUCT, VERSION, STATUS
select product, version, status
  from product_component_version
