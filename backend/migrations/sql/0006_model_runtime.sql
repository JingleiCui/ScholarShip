CREATE SCHEMA model_runtime;
REVOKE ALL ON SCHEMA model_runtime FROM PUBLIC;
GRANT USAGE ON SCHEMA model_runtime TO scholar_runtime;
CREATE TABLE model_runtime.calls (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner_id uuid NOT NULL REFERENCES scholar.users(id),
 project_id uuid NOT NULL REFERENCES scholar.projects(id), run_id uuid NOT NULL REFERENCES scholar.runs(id),
 intent text NOT NULL, request_sha256 text NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
 config_json jsonb NOT NULL, role text NOT NULL CHECK(role IN ('chat','translation','embedding','rerank','parser')),
 provider text NOT NULL, model text NOT NULL, simulated boolean NOT NULL,
 state text NOT NULL CHECK(state IN ('prepared','sent','unknown','succeeded','rejected')),
 receipt_json jsonb, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,run_id,intent)
);
ALTER TABLE model_runtime.calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_runtime.calls FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_only ON model_runtime.calls TO scholar_runtime
 USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid);
GRANT SELECT ON model_runtime.calls TO scholar_runtime;

CREATE FUNCTION model_runtime.prepare_call(rid uuid,k text,h text,c jsonb,role_name text,p text,m text,quota integer)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; project uuid; old model_runtime.calls%ROWTYPE; cid uuid;
BEGIN
 uid=billing.current_owner(true);
 SELECT project_id INTO project FROM scholar.runs WHERE id=rid AND owner_id=uid FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'run not owned' USING ERRCODE='42501'; END IF;
 IF k IS NULL OR length(k) NOT BETWEEN 1 AND 128 OR h IS NULL OR h !~ '^[0-9a-f]{64}$'
 OR c IS NULL OR jsonb_typeof(c)<>'object' OR quota IS NULL OR quota NOT BETWEEN 1 AND 200
 OR role_name IS NULL OR role_name NOT IN ('chat','translation','embedding','rerank','parser')
 OR p IS NULL OR length(p) NOT BETWEEN 1 AND 80 OR m IS NULL OR length(m) NOT BETWEEN 1 AND 160 THEN
 RAISE EXCEPTION 'invalid model intent'; END IF;
 SELECT * INTO old FROM model_runtime.calls WHERE owner_id=uid AND run_id=rid AND intent=k;
 IF FOUND THEN
  IF old.request_sha256<>h OR old.config_json<>c OR old.role<>role_name OR old.provider<>p OR old.model<>m THEN
   RAISE EXCEPTION 'model intent conflict'; END IF;
  RETURN old.id;
 END IF;
 IF (SELECT count(*) FROM model_runtime.calls WHERE run_id=rid)>=quota THEN RAISE EXCEPTION 'model request quota exhausted'; END IF;
 INSERT INTO model_runtime.calls(owner_id,project_id,run_id,intent,request_sha256,config_json,role,provider,model,simulated,state)
 VALUES(uid,project,rid,k,h,c,role_name,p,m,true,'prepared') RETURNING id INTO cid;
 RETURN cid;
END $$;

CREATE FUNCTION model_runtime.mark_sent(cid uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid;
BEGIN
 uid=billing.current_owner(true);
 UPDATE model_runtime.calls SET state='sent',updated_at=now() WHERE id=cid AND owner_id=uid AND state='prepared';
 RETURN FOUND;
END $$;

CREATE FUNCTION model_runtime.finish_call(cid uuid,new_state text,receipt jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; old model_runtime.calls%ROWTYPE;
BEGIN
 uid=billing.current_owner(false);
 SELECT * INTO old FROM model_runtime.calls WHERE id=cid AND owner_id=uid FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'call not owned' USING ERRCODE='42501'; END IF;
 IF new_state IS NULL OR new_state NOT IN ('unknown','succeeded','rejected') OR receipt IS NULL OR jsonb_typeof(receipt)<>'object' THEN RAISE EXCEPTION 'invalid model receipt'; END IF;
 IF old.state IN ('succeeded','rejected') THEN
  IF old.state=new_state AND old.receipt_json=receipt THEN RETURN; END IF;
  RAISE EXCEPTION 'conflicting model receipt';
 END IF;
 IF old.state NOT IN ('sent','unknown') THEN RAISE EXCEPTION 'call not sent'; END IF;
 UPDATE model_runtime.calls SET state=new_state,receipt_json=receipt,updated_at=now() WHERE id=cid;
END $$;

CREATE FUNCTION model_runtime.register_profile(config jsonb,hash text,dim integer) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; pid uuid; old scholar.index_profiles%ROWTYPE;
BEGIN
 uid=billing.current_owner(true);
 IF dim IS NULL OR dim NOT BETWEEN 1 AND 16384 OR hash IS NULL OR hash !~ '^[0-9a-f]{64}$'
 OR jsonb_typeof(config) IS DISTINCT FROM 'object' OR config->>'contract_revision' IS DISTINCT FROM 'embedding-profile-v1'
 OR config->>'simulated' IS DISTINCT FROM 'true' OR (config->>'dimensions')::integer IS DISTINCT FROM dim
 OR config->>'provider' IS NULL OR config->>'provider' NOT IN ('mock','openrouter','ollama')
 OR config->>'model' IS NULL OR length(config->>'model') NOT BETWEEN 1 AND 160 THEN RAISE EXCEPTION 'invalid simulation profile'; END IF;
 IF config->>'provider'='openrouter' AND config->>'model'<>'nvidia/nemotron-3.5-embed-8b:free' THEN RAISE EXCEPTION 'unreviewed embedding model'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(hash,0));
 SELECT * INTO old FROM scholar.index_profiles WHERE fingerprint=hash;
 IF FOUND THEN
  IF old.config_json<>config OR old.dimensions<>dim THEN RAISE EXCEPTION 'profile conflict'; END IF;
  RETURN old.id;
 END IF;
 INSERT INTO scholar.index_profiles(profile_key,provider,model_name,dimensions,metric,chunker_revision,lexical_revision,config_json,fingerprint)
 VALUES('model_'||hash,config->>'provider',config->>'model',dim,'cosine','block-unicode-boundary-v1','nfkc-word-cjk-bigram-v1',config,hash) RETURNING id INTO pid;
 RETURN pid;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA model_runtime FROM PUBLIC;
GRANT EXECUTE ON FUNCTION model_runtime.prepare_call(uuid,text,text,jsonb,text,text,text,integer),model_runtime.mark_sent(uuid),
 model_runtime.finish_call(uuid,text,jsonb),model_runtime.register_profile(jsonb,text,integer) TO scholar_runtime;
