-- Forward-only storage policy hardening.

begin;

-- Intentionally no table-level REVOKE/GRANT here: platform-managed storage
-- privileges cannot be altered by the postgres migration role.

commit;
