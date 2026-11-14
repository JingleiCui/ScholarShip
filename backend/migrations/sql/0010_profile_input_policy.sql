-- Preserve 0009; support protocols whose query/document policy is same_text.
CREATE OR REPLACE FUNCTION model_runtime.register_real_profile(cid uuid,config jsonb,hash text,dim integer) RETURNS uuid
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
