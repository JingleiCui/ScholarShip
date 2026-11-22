CREATE TABLE model_runtime.local_admissions (
 call_id uuid PRIMARY KEY REFERENCES model_runtime.calls(id),
 owner_id uuid NOT NULL REFERENCES scholar.users(id),
 maximum_tokens bigint NOT NULL CHECK(maximum_tokens BETWEEN 1 AND 1000000),
 actual_tokens bigint CHECK(actual_tokens>=0), settled boolean NOT NULL DEFAULT false
);
ALTER TABLE model_runtime.local_admissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_runtime.local_admissions FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_only ON model_runtime.local_admissions TO scholar_runtime USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid);
GRANT SELECT ON model_runtime.local_admissions TO scholar_runtime;

CREATE FUNCTION model_runtime.prepare_local(rid uuid,k text,h text,c jsonb,role_name text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; r scholar.runs%ROWTYPE; old model_runtime.calls%ROWTYPE; cid uuid; spec jsonb; quota integer;
BEGIN
 uid=billing.current_owner(true);
 SELECT * INTO r FROM scholar.runs WHERE id=rid AND owner_id=uid FOR UPDATE;
 IF NOT FOUND OR r.config_json->>'mode' IS DISTINCT FROM 'local_inference' OR r.config_json->'models' IS DISTINCT FROM c THEN RAISE EXCEPTION 'local run scope mismatch'; END IF;
 spec=c->'roles'->role_name; quota=(c->>'max_requests_per_run')::integer;
 IF role_name IS NULL OR role_name NOT IN ('embedding','rerank','chat','translation')
 OR k IS NULL OR length(k) NOT BETWEEN 1 AND 128 OR h IS NULL OR h !~ '^[a-f0-9]{64}$'
 OR quota IS NULL OR quota NOT BETWEEN 1 AND 200
 OR coalesce(spec->>'base_url','') !~ '^http://127[.]0[.]0[.]1:[0-9]{1,5}(/[^?#@[:space:]]*)?$'
 OR spec->>'provider' IS NULL OR spec->>'model' IS NULL THEN RAISE EXCEPTION 'invalid local intent'; END IF;
 SELECT * INTO old FROM model_runtime.calls WHERE owner_id=uid AND run_id=rid AND intent=k;
 IF FOUND THEN
  IF old.simulated OR old.grant_id IS NOT NULL OR old.request_sha256<>h OR old.config_json<>c OR old.role<>role_name THEN RAISE EXCEPTION 'local intent conflict'; END IF;
  RETURN old.id;
 END IF;
 IF (SELECT count(*) FROM model_runtime.calls WHERE run_id=rid)>=quota THEN RAISE EXCEPTION 'local run quota exhausted'; END IF;
 INSERT INTO model_runtime.calls(owner_id,project_id,run_id,intent,request_sha256,config_json,role,provider,model,simulated,state)
 VALUES(uid,r.project_id,rid,k,h,c,role_name,spec->>'provider',spec->>'model',false,'prepared') RETURNING id INTO cid;
 RETURN cid;
END $$;

CREATE FUNCTION model_runtime.admit_local(cid uuid,sid uuid,maximum bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; c model_runtime.calls%ROWTYPE;
BEGIN
 uid=billing.current_owner(true);
 SELECT * INTO c FROM model_runtime.calls WHERE id=cid AND owner_id=uid FOR UPDATE;
 IF NOT FOUND OR c.simulated OR c.grant_id IS NOT NULL OR c.state<>'prepared'
 OR maximum IS NULL OR maximum NOT BETWEEN 1 AND 1000000
 OR NOT EXISTS(SELECT 1 FROM scholar.runs r WHERE r.id=c.run_id AND r.owner_id=uid AND r.config_json->>'mode'='local_inference' AND r.config_json->'models'=c.config_json)
 OR NOT EXISTS(SELECT 1 FROM scholar.run_steps WHERE id=sid AND run_id=c.run_id AND owner_id=uid AND project_id=c.project_id)
 THEN RAISE EXCEPTION 'local admission closed'; END IF;
 INSERT INTO model_runtime.local_admissions(call_id,owner_id,maximum_tokens) VALUES(cid,uid,maximum);
 UPDATE model_runtime.calls SET state='sent',updated_at=now() WHERE id=cid;
END $$;

CREATE FUNCTION model_runtime.settle_local(cid uuid,actual bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; a model_runtime.local_admissions%ROWTYPE; c model_runtime.calls%ROWTYPE;
BEGIN
 uid=billing.current_owner(false);
 SELECT * INTO c FROM model_runtime.calls WHERE id=cid AND owner_id=uid FOR UPDATE;
 SELECT * INTO a FROM model_runtime.local_admissions WHERE call_id=cid AND owner_id=uid FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'local admission not owned'; END IF;
 IF a.settled THEN RETURN; END IF;
 IF c.state<>'succeeded' OR actual IS NULL OR actual<0 OR actual>a.maximum_tokens
 OR actual IS DISTINCT FROM (c.receipt_json->'usage'->>'total_tokens')::bigint THEN RAISE EXCEPTION 'local usage mismatch'; END IF;
 UPDATE model_runtime.local_admissions SET settled=true,actual_tokens=actual WHERE call_id=cid;
END $$;
REVOKE ALL ON FUNCTION model_runtime.prepare_local(uuid,text,text,jsonb,text),model_runtime.admit_local(uuid,uuid,bigint),model_runtime.settle_local(uuid,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION model_runtime.prepare_local(uuid,text,text,jsonb,text),model_runtime.admit_local(uuid,uuid,bigint),model_runtime.settle_local(uuid,bigint) TO scholar_runtime;

-- Preserve 0009; support protocols whose query/document policy is same_text.
CREATE OR REPLACE FUNCTION model_runtime.register_real_profile(cid uuid,config jsonb,hash text,dim integer) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; c model_runtime.calls%ROWTYPE; old scholar.index_profiles%ROWTYPE; pid uuid;
BEGIN
 uid=billing.current_owner(true);
 SELECT * INTO c FROM model_runtime.calls WHERE id=cid AND owner_id=uid;
 IF NOT FOUND OR c.state<>'succeeded' OR c.simulated OR c.role<>'embedding'
 OR NOT (c.network_slot_consumed OR EXISTS(SELECT 1 FROM model_runtime.production_admissions WHERE call_id=c.id) OR EXISTS(SELECT 1 FROM model_runtime.local_admissions WHERE call_id=c.id AND settled))
 OR jsonb_typeof(config->'adapter_binding') IS DISTINCT FROM 'object'
 OR c.receipt_json->>'simulated' IS DISTINCT FROM 'false'
 OR c.receipt_json->>'provider' IS DISTINCT FROM c.provider OR c.receipt_json->>'requested_model' IS DISTINCT FROM c.model
 OR config->>'simulated' IS DISTINCT FROM 'false' OR config->>'contract_revision' IS DISTINCT FROM 'embedding-profile-v1'
 OR config->>'provider' IS DISTINCT FROM c.provider OR config->>'model' IS DISTINCT FROM c.model
 OR config->>'base_url' IS DISTINCT FROM c.config_json->'roles'->'embedding'->>'base_url'
 OR config->'adapter_binding' IS DISTINCT FROM c.config_json->'bindings'->'embedding'
 OR config->'input_type_policy' IS DISTINCT FROM coalesce(config->'adapter_binding'->'definition'->'embedding_input_types','"same_text"'::jsonb)
 OR dim IS NULL OR dim NOT BETWEEN 1 AND 16384 OR (config->>'dimensions')::integer IS DISTINCT FROM dim
 OR (c.receipt_json->>'dimensions')::integer IS DISTINCT FROM dim
 OR hash IS NULL OR hash !~ '^[a-f0-9]{64}$' THEN RAISE EXCEPTION 'verified real embedding receipt required'; END IF;
 IF NOT EXISTS(SELECT 1 FROM scholar.projects WHERE id=c.project_id AND owner_id=uid) THEN RAISE EXCEPTION 'profile project scope mismatch'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(hash,0));
 SELECT * INTO old FROM scholar.index_profiles WHERE fingerprint=hash;
 IF FOUND THEN
  IF old.config_json<>config OR old.dimensions<>dim THEN RAISE EXCEPTION 'real profile conflict'; END IF;
  INSERT INTO model_runtime.real_profile_proofs VALUES(old.id,uid,c.project_id,cid) ON CONFLICT DO NOTHING;
  RETURN old.id;
 END IF;
 INSERT INTO scholar.index_profiles(profile_key,provider,model_name,dimensions,metric,chunker_revision,lexical_revision,config_json,fingerprint)
 VALUES('real_'||hash,c.provider,c.model,dim,'cosine','block-unicode-boundary-v1','nfkc-word-cjk-bigram-v1',config,hash) RETURNING id INTO pid;
 INSERT INTO model_runtime.real_profile_proofs VALUES(pid,uid,c.project_id,cid);
 INSERT INTO scholar.audit_events(owner_id,project_id,action,subject_json,safe_payload) VALUES(uid,c.project_id,'real_embedding_profile_registered',jsonb_build_object('profile_id',pid),jsonb_build_object('call_id',cid,'dimensions',dim));
 RETURN pid;
END $$;

REVOKE ALL ON FUNCTION model_runtime.register_real_profile(uuid,jsonb,text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION model_runtime.register_real_profile(uuid,jsonb,text,integer) TO scholar_runtime;
