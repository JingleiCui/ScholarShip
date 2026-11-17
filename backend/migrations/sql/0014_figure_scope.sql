CREATE OR REPLACE FUNCTION model_runtime.production_scope_allowed(rid uuid,scope jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE r scholar.runs%ROWTYPE; ids jsonb; value jsonb;
BEGIN
 SELECT * INTO r FROM scholar.runs WHERE id=rid AND owner_id=billing.current_owner(true);
 IF NOT FOUND THEN RETURN false; END IF;
 IF r.kind='research_studio' AND r.config_json->>'operation'='figure' THEN
  IF jsonb_typeof(scope->'block_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(scope->'block_ids')<1
     OR jsonb_typeof(r.input_json->'arguments'->'block_ids') IS DISTINCT FROM 'array'
     OR jsonb_array_length(r.input_json->'arguments'->'block_ids')<>1
     OR NOT ((scope->'block_ids') @> (r.input_json->'arguments'->'block_ids')) THEN RETURN false; END IF;
  RETURN EXISTS(SELECT 1 FROM scholar.document_blocks b JOIN scholar.document_parses p ON p.id=b.parse_id
    WHERE b.id::text=(r.input_json->'arguments'->'block_ids'->>0) AND b.owner_id=r.owner_id
    AND b.project_id=r.project_id AND b.kind='figure' AND b.asset_file_id IS NOT NULL AND p.status='ready'
    AND (scope->'parse_ids') @> jsonb_build_array(b.parse_id::text));
 END IF;
 IF scope='{}'::jsonb THEN RETURN true; END IF;
 IF jsonb_typeof(scope->'parse_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(scope->'parse_ids')<1 THEN RETURN false; END IF;
 IF r.kind='research_build' THEN
  SELECT coalesce(jsonb_agg(p->'parse_id'),'[]') INTO ids FROM jsonb_array_elements(r.input_json->'parses') p;
 ELSIF r.kind='research_qa' THEN
  SELECT coalesce(jsonb_agg(p->'parse_id'),'[]') INTO ids FROM jsonb_array_elements(r.input_scope_json->'generations') p;
 ELSIF r.kind='research_studio' AND r.config_json->>'operation'='translate' THEN
  ids=jsonb_build_array(r.input_json->'arguments'->'parse_id');
 ELSIF r.kind='research_studio' AND r.config_json->>'operation' IN ('summary','draft','direction') THEN
  ids=r.input_json->'arguments'->'parse_ids';
 ELSE RETURN false;
 END IF;
 IF jsonb_typeof(ids) IS DISTINCT FROM 'array' OR jsonb_array_length(ids)<1 THEN RETURN false; END IF;
 FOR value IN SELECT * FROM jsonb_array_elements(ids) LOOP
  IF NOT (scope->'parse_ids' @> jsonb_build_array(value)) THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;
