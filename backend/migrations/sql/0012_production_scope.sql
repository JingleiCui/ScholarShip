ALTER TABLE model_runtime.production_policies ADD COLUMN data_scope_json jsonb NOT NULL DEFAULT '{}';
CREATE FUNCTION model_runtime.production_scope_allowed(rid uuid,scope jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE r scholar.runs%ROWTYPE; ids jsonb; value jsonb;
BEGIN
 SELECT * INTO r FROM scholar.runs WHERE id=rid AND owner_id=billing.current_owner(true);
 IF NOT FOUND THEN RETURN false; END IF;
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
REVOKE ALL ON FUNCTION model_runtime.production_scope_allowed(uuid,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION model_runtime.production_scope_allowed(uuid,jsonb) TO scholar_runtime;
CREATE OR REPLACE FUNCTION model_runtime.admit_production(cid uuid,sid uuid,maximum bigint) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; c model_runtime.calls%ROWTYPE; p model_runtime.production_policies%ROWTYPE; bid uuid;
BEGIN
 uid=billing.current_owner(true);
 SELECT * INTO c FROM model_runtime.calls WHERE id=cid AND owner_id=uid FOR UPDATE;
 IF NOT FOUND OR c.simulated OR c.grant_id IS NOT NULL OR c.state<>'prepared' OR maximum IS NULL OR maximum NOT BETWEEN 1 AND 1000000 THEN RAISE EXCEPTION 'production call not executable'; END IF;
 IF NOT EXISTS(SELECT 1 FROM scholar.run_steps WHERE id=sid AND run_id=c.run_id AND owner_id=uid AND project_id=c.project_id) THEN RAISE EXCEPTION 'production step scope mismatch'; END IF;
 SELECT * INTO p FROM model_runtime.production_policies WHERE owner_id=uid AND project_id=c.project_id AND provider=c.provider AND model=c.model AND role=c.role AND enabled AND free_verified AND starts_at<=now() AND ends_at>now() ORDER BY starts_at,id LIMIT 1 FOR UPDATE;
 IF NOT FOUND OR NOT model_runtime.production_scope_allowed(c.run_id,p.data_scope_json) OR p.requests_used>=p.request_limit OR p.tokens_used+p.tokens_reserved+maximum>p.token_limit THEN RAISE EXCEPTION 'production quota closed'; END IF;
 -- A one-micro-CNY safety hold engages the existing shared/account gates; it is NOT a token price.
 bid=billing.reserve_call(sid,'production:'||cid,c.provider,c.role,c.request_sha256,0.000001,jsonb_build_object('currency','CNY','free_verified',true,'policy_id',p.id,'safety_hold',true),false);
 IF NOT billing.mark_sent(bid) THEN RAISE EXCEPTION 'production billing not executable'; END IF;
 INSERT INTO model_runtime.production_admissions(call_id,policy_id,billing_call_id,owner_id,maximum_tokens) VALUES(cid,p.id,bid,uid,maximum);
 UPDATE model_runtime.production_policies SET requests_used=requests_used+1,tokens_reserved=tokens_reserved+maximum WHERE id=p.id;
 UPDATE model_runtime.calls SET state='sent',updated_at=now() WHERE id=cid;
 RETURN bid;
END $$;

CREATE FUNCTION model_runtime.suspend_production(cid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; c model_runtime.calls%ROWTYPE; ref text;
BEGIN
 uid=billing.current_owner(false);
 SELECT * INTO c FROM model_runtime.calls WHERE id=cid AND owner_id=uid;
 IF NOT FOUND THEN RAISE EXCEPTION 'call not owned'; END IF;
 SELECT p.approval_reference INTO ref FROM model_runtime.production_policies p JOIN model_runtime.production_admissions a ON a.policy_id=p.id WHERE a.call_id=cid AND a.owner_id=uid;
 IF ref IS NOT NULL THEN
  UPDATE model_runtime.production_policies SET enabled=false WHERE owner_id=uid AND project_id=c.project_id AND approval_reference=ref;
 END IF;
END $$;
REVOKE ALL ON FUNCTION model_runtime.suspend_production(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION model_runtime.suspend_production(uuid) TO scholar_runtime;
