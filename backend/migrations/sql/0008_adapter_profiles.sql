CREATE OR REPLACE FUNCTION model_runtime.register_profile(config jsonb,hash text,dim integer) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; pid uuid; old scholar.index_profiles%ROWTYPE;
BEGIN
 uid=billing.current_owner(true);
 IF dim IS NULL OR dim NOT BETWEEN 1 AND 16384 OR hash IS NULL OR hash !~ '^[0-9a-f]{64}$'
 OR jsonb_typeof(config) IS DISTINCT FROM 'object' OR config->>'contract_revision' IS DISTINCT FROM 'embedding-profile-v1'
 OR config->>'simulated' IS DISTINCT FROM 'true' OR (config->>'dimensions')::integer IS DISTINCT FROM dim
 OR config->>'provider' IS NULL OR config->>'provider' !~ '^[a-z][a-z0-9_]{0,63}$'
 OR config->>'model' IS NULL OR length(config->>'model') NOT BETWEEN 1 AND 160 THEN RAISE EXCEPTION 'invalid simulation profile'; END IF;
 IF config ? 'adapter_binding' THEN
  IF jsonb_typeof(config->'adapter_binding') IS DISTINCT FROM 'object'
   OR jsonb_typeof(config->'adapter_binding'->'definition') IS DISTINCT FROM 'object'
   OR length(coalesce(config->'adapter_binding'->>'protocol','')) NOT BETWEEN 1 AND 100
   OR length(coalesce(config->'adapter_binding'->>'revision','')) NOT BETWEEN 1 AND 100
   OR config->'adapter_binding'->'definition'->'roles'->>'embedding' IS DISTINCT FROM config->'adapter_binding'->>'protocol'
   OR NOT coalesce((config->'adapter_binding'->'definition'->'base_urls') ? (config->>'base_url'),false)
   THEN RAISE EXCEPTION 'invalid embedding adapter binding'; END IF;
  IF config->'adapter_binding'->'definition'->>'free_only'='true' AND config->>'model' NOT LIKE '%:free'
   THEN RAISE EXCEPTION 'paid embedding route rejected'; END IF;
 ELSE
  -- Compatibility for frozen v1 simulation profiles; no new vendor bypass.
  IF config->>'provider' NOT IN ('mock','openrouter','ollama') OR
   (config->>'provider'='openrouter' AND config->>'model'<>'nvidia/nemotron-3.5-embed-8b:free')
   THEN RAISE EXCEPTION 'adapter binding required'; END IF;
 END IF;
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
REVOKE ALL ON FUNCTION model_runtime.register_profile(jsonb,text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION model_runtime.register_profile(jsonb,text,integer) TO scholar_runtime;
