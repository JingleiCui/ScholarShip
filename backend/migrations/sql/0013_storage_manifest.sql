-- Internal integrity inventory: no content, credentials, arbitrary SQL or mutation.
-- Global deployment storage spans owners; a tenant-filtered empty scan cannot prove integrity.
CREATE FUNCTION scholar.storage_manifest()
RETURNS TABLE(storage_key text, byte_size bigint, sha256 text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
SELECT f.storage_key::text,f.byte_size::bigint,f.sha256::text
FROM scholar.file_objects AS f WHERE f.state='ready';
$$;
REVOKE ALL ON FUNCTION scholar.storage_manifest() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scholar.storage_manifest() TO scholar_runtime;
