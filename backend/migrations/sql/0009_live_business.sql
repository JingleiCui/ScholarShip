-- No policy is provisioned or enabled by this migration. Old grants are untouched.
CREATE TABLE model_runtime.production_policies (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner_id uuid NOT NULL REFERENCES scholar.users(id),
 project_id uuid NOT NULL REFERENCES scholar.projects(id), provider text NOT NULL, model text NOT NULL,
 role text NOT NULL CHECK(role IN ('embedding','rerank','chat','translation')),
 enabled boolean NOT NULL DEFAULT false, free_verified boolean NOT NULL DEFAULT false,
 starts_at timestamptz NOT NULL DEFAULT now(), ends_at timestamptz NOT NULL,
 request_limit integer NOT NULL CHECK(request_limit>0), token_limit bigint NOT NULL CHECK(token_limit>0),
 requests_used integer NOT NULL DEFAULT 0 CHECK(requests_used>=0),
 tokens_reserved bigint NOT NULL DEFAULT 0 CHECK(tokens_reserved>=0),
 tokens_used bigint NOT NULL DEFAULT 0 CHECK(tokens_used>=0),
 approval_reference text NOT NULL CHECK(length(approval_reference)>0),
 CHECK(ends_at>starts_at), CHECK(requests_used<=request_limit), CHECK(tokens_reserved+tokens_used<=token_limit)
);
ALTER TABLE model_runtime.production_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_runtime.production_policies FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_only ON model_runtime.production_policies TO scholar_runtime USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid);
GRANT SELECT ON model_runtime.production_policies TO scholar_runtime;
CREATE TABLE model_runtime.production_admissions (
 call_id uuid PRIMARY KEY REFERENCES model_runtime.calls(id), policy_id uuid NOT NULL REFERENCES model_runtime.production_policies(id),
 billing_call_id uuid NOT NULL UNIQUE REFERENCES scholar.external_calls(id), owner_id uuid NOT NULL REFERENCES scholar.users(id),
 maximum_tokens bigint NOT NULL CHECK(maximum_tokens>0), settled boolean NOT NULL DEFAULT false
);
ALTER TABLE model_runtime.production_admissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_runtime.production_admissions FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_only ON model_runtime.production_admissions TO scholar_runtime USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid);
GRANT SELECT ON model_runtime.production_admissions TO scholar_runtime;

CREATE FUNCTION model_runtime.prepare_production(rid uuid,k text,h text,c jsonb,role_name text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; r scholar.runs%ROWTYPE; old model_runtime.calls%ROWTYPE; cid uuid; spec jsonb;
BEGIN
 uid=billing.current_owner(true);
 SELECT * INTO r FROM scholar.runs WHERE id=rid AND owner_id=uid FOR UPDATE;
 IF NOT FOUND OR r.config_json->>'mode' IS DISTINCT FROM 'cloud' OR r.config_json->'models' IS DISTINCT FROM c THEN RAISE EXCEPTION 'production run scope mismatch'; END IF;
 spec=c->'roles'->role_name;
 IF role_name IS NULL OR role_name NOT IN ('embedding','rerank','chat','translation') OR k IS NULL OR length(k) NOT BETWEEN 1 AND 128
 OR h IS NULL OR h !~ '^[a-f0-9]{64}$' OR spec->>'provider' IS NULL OR spec->>'model' IS NULL THEN RAISE EXCEPTION 'invalid production intent'; END IF;
 SELECT * INTO old FROM model_runtime.calls WHERE owner_id=uid AND run_id=rid AND intent=k;
 IF FOUND THEN
  IF old.simulated OR old.grant_id IS NOT NULL OR old.request_sha256<>h OR old.config_json<>c OR old.role<>role_name THEN RAISE EXCEPTION 'production intent conflict'; END IF;
  RETURN old.id;
 END IF;
 IF (SELECT count(*) FROM model_runtime.calls WHERE run_id=rid)>=(c->>'max_requests_per_run')::integer THEN RAISE EXCEPTION 'run quota exhausted'; END IF;
 IF NOT EXISTS(SELECT 1 FROM model_runtime.production_policies WHERE owner_id=uid AND project_id=r.project_id AND provider=spec->>'provider' AND model=spec->>'model' AND role=role_name AND enabled AND free_verified AND starts_at<=now() AND ends_at>now() AND requests_used<request_limit) THEN RAISE EXCEPTION 'production policy closed'; END IF;
 INSERT INTO model_runtime.calls(owner_id,project_id,run_id,intent,request_sha256,config_json,role,provider,model,simulated,state)
 VALUES(uid,r.project_id,rid,k,h,c,role_name,spec->>'provider',spec->>'model',false,'prepared') RETURNING id INTO cid;
 RETURN cid;
END $$;

CREATE FUNCTION model_runtime.admit_production(cid uuid,sid uuid,maximum bigint) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; c model_runtime.calls%ROWTYPE; p model_runtime.production_policies%ROWTYPE; bid uuid;
BEGIN
 uid=billing.current_owner(true);
 SELECT * INTO c FROM model_runtime.calls WHERE id=cid AND owner_id=uid FOR UPDATE;
 IF NOT FOUND OR c.simulated OR c.grant_id IS NOT NULL OR c.state<>'prepared' OR maximum IS NULL OR maximum NOT BETWEEN 1 AND 1000000 THEN RAISE EXCEPTION 'production call not executable'; END IF;
 IF NOT EXISTS(SELECT 1 FROM scholar.run_steps WHERE id=sid AND run_id=c.run_id AND owner_id=uid AND project_id=c.project_id) THEN RAISE EXCEPTION 'production step scope mismatch'; END IF;
 SELECT * INTO p FROM model_runtime.production_policies WHERE owner_id=uid AND project_id=c.project_id AND provider=c.provider AND model=c.model AND role=c.role AND enabled AND free_verified AND starts_at<=now() AND ends_at>now() ORDER BY starts_at,id LIMIT 1 FOR UPDATE;
 IF NOT FOUND OR p.requests_used>=p.request_limit OR p.tokens_used+p.tokens_reserved+maximum>p.token_limit THEN RAISE EXCEPTION 'production quota closed'; END IF;
 -- A one-micro-CNY safety hold engages the existing shared/account gates; it is NOT a token price.
 bid=billing.reserve_call(sid,'production:'||cid,c.provider,c.role,c.request_sha256,0.000001,jsonb_build_object('currency','CNY','free_verified',true,'policy_id',p.id,'safety_hold',true),false);
 IF NOT billing.mark_sent(bid) THEN RAISE EXCEPTION 'production billing not executable'; END IF;
 INSERT INTO model_runtime.production_admissions(call_id,policy_id,billing_call_id,owner_id,maximum_tokens) VALUES(cid,p.id,bid,uid,maximum);
 UPDATE model_runtime.production_policies SET requests_used=requests_used+1,tokens_reserved=tokens_reserved+maximum WHERE id=p.id;
 UPDATE model_runtime.calls SET state='sent',updated_at=now() WHERE id=cid;
 RETURN bid;
END $$;

CREATE FUNCTION model_runtime.settle_production(cid uuid,actual bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; a model_runtime.production_admissions%ROWTYPE; c model_runtime.calls%ROWTYPE;
BEGIN
 uid=billing.current_owner(false);
 SELECT * INTO c FROM model_runtime.calls WHERE id=cid AND owner_id=uid FOR UPDATE;
 SELECT * INTO a FROM model_runtime.production_admissions WHERE call_id=cid AND owner_id=uid FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'admission not owned'; END IF;
 IF a.settled THEN RETURN; END IF;
 IF c.state<>'succeeded' OR actual IS NULL OR actual<0 OR actual>a.maximum_tokens THEN RAISE EXCEPTION 'usage reconciliation required'; END IF;
 -- Verify supplied usage against the stored successful receipt.
 IF actual IS DISTINCT FROM (c.receipt_json->'usage'->>'total_tokens')::bigint THEN RAISE EXCEPTION 'usage mismatch'; END IF;
 PERFORM billing.settle_call(a.billing_call_id,0,jsonb_build_object('model_call_id',cid,'usage',c.receipt_json->'usage','free_verified',true),encode(sha256(convert_to(c.receipt_json::text,'UTF8')),'hex'));
 UPDATE model_runtime.production_policies SET tokens_reserved=tokens_reserved-a.maximum_tokens,tokens_used=tokens_used+actual WHERE id=a.policy_id;
 UPDATE model_runtime.production_admissions SET settled=true WHERE call_id=cid;
END $$;

CREATE TABLE model_runtime.real_profile_proofs (
 profile_id uuid NOT NULL REFERENCES scholar.index_profiles(id), owner_id uuid NOT NULL REFERENCES scholar.users(id),
 project_id uuid NOT NULL REFERENCES scholar.projects(id), call_id uuid NOT NULL REFERENCES model_runtime.calls(id),
 PRIMARY KEY(profile_id,owner_id,project_id,call_id)
);
ALTER TABLE model_runtime.real_profile_proofs ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_runtime.real_profile_proofs FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_only ON model_runtime.real_profile_proofs TO scholar_runtime USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid);
GRANT SELECT ON model_runtime.real_profile_proofs TO scholar_runtime;

CREATE FUNCTION model_runtime.register_real_profile(cid uuid,config jsonb,hash text,dim integer) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; c model_runtime.calls%ROWTYPE; old scholar.index_profiles%ROWTYPE; pid uuid;
BEGIN
 uid=billing.current_owner(true);
 SELECT * INTO c FROM model_runtime.calls WHERE id=cid AND owner_id=uid;
 IF NOT FOUND OR c.state<>'succeeded' OR c.simulated OR c.role<>'embedding'
 OR NOT (c.network_slot_consumed OR EXISTS(SELECT 1 FROM model_runtime.production_admissions WHERE call_id=c.id))
 OR jsonb_typeof(config->'adapter_binding') IS DISTINCT FROM 'object'
 OR c.receipt_json->>'simulated' IS DISTINCT FROM 'false'
 OR c.receipt_json->>'provider' IS DISTINCT FROM c.provider OR c.receipt_json->>'requested_model' IS DISTINCT FROM c.model
 OR config->>'simulated' IS DISTINCT FROM 'false' OR config->>'contract_revision' IS DISTINCT FROM 'embedding-profile-v1'
 OR config->>'provider' IS DISTINCT FROM c.provider OR config->>'model' IS DISTINCT FROM c.model
 OR config->>'base_url' IS DISTINCT FROM c.config_json->'roles'->'embedding'->>'base_url'
 OR config->'adapter_binding' IS DISTINCT FROM c.config_json->'bindings'->'embedding'
 OR config->'input_type_policy' IS DISTINCT FROM config->'adapter_binding'->'definition'->'embedding_input_types'
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
REVOKE ALL ON FUNCTION model_runtime.prepare_production(uuid,text,text,jsonb,text),model_runtime.admit_production(uuid,uuid,bigint),model_runtime.settle_production(uuid,bigint),model_runtime.register_real_profile(uuid,jsonb,text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION model_runtime.prepare_production(uuid,text,text,jsonb,text),model_runtime.admit_production(uuid,uuid,bigint),model_runtime.settle_production(uuid,bigint),model_runtime.register_real_profile(uuid,jsonb,text,integer) TO scholar_runtime;

CREATE OR REPLACE FUNCTION scholar.claim_outbox_v2(batch_size integer)
RETURNS TABLE(id uuid,owner_id uuid,project_id uuid,run_id uuid,lease_token uuid,command text)
LANGUAGE sql SECURITY DEFINER SET search_path=scholar,pg_temp AS $$
WITH picked AS (
 SELECT o.id FROM scholar.task_outbox o JOIN scholar.runs r ON r.id=o.run_id
 WHERE o.command IN ('system_probe','preview_parse','offline_build','offline_qa','research_build','research_qa') AND o.available_at<=now()
 AND (o.lease_until IS NULL OR o.lease_until<now()) AND (r.status='queued' OR (r.status='running' AND r.lease_until<now()))
 ORDER BY o.available_at,o.id LIMIT least(greatest(batch_size,1),20) FOR UPDATE OF o SKIP LOCKED
) UPDATE scholar.task_outbox o SET lease_token=gen_random_uuid(),lease_until=now()+interval '30 seconds',delivery_attempts=o.delivery_attempts+1,updated_at=now() FROM picked WHERE o.id=picked.id
RETURNING o.id,o.owner_id,o.project_id,o.run_id,o.lease_token,o.command;
$$;
