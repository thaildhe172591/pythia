-- Purpose: the database character sets and language settings. A non-Unicode
--          character set cannot hold Vietnamese text in VARCHAR2, which
--          decides how literals and columns must be written.
-- Binds:   (none)
-- Returns: PARAMETER, VALUE
select parameter, value
  from nls_database_parameters
 where parameter in ('NLS_CHARACTERSET', 'NLS_NCHAR_CHARACTERSET',
                     'NLS_LANGUAGE', 'NLS_TERRITORY', 'NLS_LENGTH_SEMANTICS')
 order by parameter
