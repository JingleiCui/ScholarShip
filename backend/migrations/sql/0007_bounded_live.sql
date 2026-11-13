CREATE TABLE model_runtime.grants (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), approval_key text NOT NULL UNIQUE,
 owner_id uuid NOT NULL REFERENCES scholar.users(id), project_id uuid NOT NULL REFERENCES scholar.projects(id),
 run_id uuid NOT NULL UNIQUE REFERENCES scholar.runs(id), manifest_json jsonb NOT NULL,
 manifest_sha256 text NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
 max_requests integer NOT NULL CHECK(max_requests BETWEEN 1 AND 5),
 max_output_tokens integer NOT NULL CHECK(max_output_tokens BETWEEN 1 AND 256),
 used_requests integer NOT NULL DEFAULT 0 CHECK(used_requests>=0 AND used_requests<=max_requests),
 state text NOT NULL CHECK(state IN ('active','stopped','completed','expired')),
 expires_at timestamptz NOT NULL, stop_code text, preflight_json jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE model_runtime.grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_runtime.grants FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_only ON model_runtime.grants TO scholar_runtime
 USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid);
GRANT SELECT ON model_runtime.grants TO scholar_runtime;
ALTER TABLE model_runtime.calls ADD COLUMN grant_id uuid REFERENCES model_runtime.grants(id);
ALTER TABLE model_runtime.calls ADD COLUMN network_slot_consumed boolean NOT NULL DEFAULT false;

CREATE FUNCTION model_runtime.prepare_live_call(gid uuid,k text,h text,c jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; g model_runtime.grants%ROWTYPE; a jsonb; old model_runtime.calls%ROWTYPE; cid uuid;
BEGIN
 uid=billing.current_owner(true);
 SELECT * INTO g FROM model_runtime.grants WHERE id=gid AND owner_id=uid FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'grant not owned' USING ERRCODE='42501'; END IF;
 a=g.manifest_json->'requests'->k;
 IF a IS NULL OR a->>'request_sha256' IS DISTINCT FROM h OR g.manifest_json->'snapshot' IS DISTINCT FROM c THEN
 RAISE EXCEPTION 'request outside approved manifest' USING ERRCODE='42501'; END IF;
 SELECT * INTO old FROM model_runtime.calls WHERE owner_id=uid AND run_id=g.run_id AND intent=k;
 IF FOUND THEN
  IF old.grant_id IS DISTINCT FROM gid OR old.request_sha256<>h OR old.config_json<>c THEN RAISE EXCEPTION 'live intent conflict'; END IF;
  RETURN old.id;
 END IF;
 IF g.state<>'active' OR now()>=g.expires_at OR g.used_requests>=g.max_requests THEN RAISE EXCEPTION 'live grant closed' USING ERRCODE='42501'; END IF;
 IF EXISTS(SELECT 1 FROM model_runtime.calls WHERE grant_id=gid AND state IN ('prepared','sent','unknown','rejected')) THEN
 RAISE EXCEPTION 'previous live call incomplete' USING ERRCODE='42501'; END IF;
 INSERT INTO model_runtime.calls(owner_id,project_id,run_id,intent,request_sha256,config_json,role,provider,model,simulated,state,grant_id)
 VALUES(uid,g.project_id,g.run_id,k,h,c,a->>'role',a->>'provider',a->>'model',false,'prepared',gid) RETURNING id INTO cid;
 RETURN cid;
END $$;

CREATE FUNCTION model_runtime.consume_live_slot(cid uuid,wire_hash text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; g model_runtime.grants%ROWTYPE; c model_runtime.calls%ROWTYPE;
BEGIN
 uid=billing.current_owner(true);
 SELECT * INTO g FROM model_runtime.grants WHERE id=(SELECT grant_id FROM model_runtime.calls WHERE id=cid AND owner_id=uid) FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'live grant not owned' USING ERRCODE='42501'; END IF;
 SELECT * INTO c FROM model_runtime.calls WHERE id=cid AND owner_id=uid FOR UPDATE;
 IF c.state<>'prepared' OR c.network_slot_consumed OR g.state<>'active' OR now()>=g.expires_at OR g.used_requests>=g.max_requests
 OR (g.manifest_json->'requests'->c.intent->>'wire_sha256') IS DISTINCT FROM wire_hash THEN
 RAISE EXCEPTION 'live dispatch denied' USING ERRCODE='42501'; END IF;
 UPDATE model_runtime.grants SET used_requests=used_requests+1,updated_at=now() WHERE id=g.id;
 UPDATE model_runtime.calls SET network_slot_consumed=true,state='sent',updated_at=now() WHERE id=cid;
END $$;

CREATE FUNCTION model_runtime.stop_grant(gid uuid,reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid;
BEGIN
 uid=billing.current_owner(false);
 IF reason IS NULL OR length(reason) NOT BETWEEN 1 AND 120 OR reason !~ '^[a-zA-Z0-9_]+$' THEN RAISE EXCEPTION 'invalid stop reason'; END IF;
 UPDATE model_runtime.grants SET state='stopped',stop_code=reason,updated_at=now() WHERE id=gid AND owner_id=uid AND state='active';
END $$;

CREATE FUNCTION model_runtime.complete_grant(gid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=model_runtime,scholar,billing,pg_temp AS $$
DECLARE uid uuid; g model_runtime.grants%ROWTYPE;
BEGIN
 uid=billing.current_owner(false);
 SELECT * INTO g FROM model_runtime.grants WHERE id=gid AND owner_id=uid FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'grant not owned' USING ERRCODE='42501'; END IF;
 IF g.state='completed' THEN RETURN; END IF;
 IF g.state<>'active' OR EXISTS(SELECT 1 FROM model_runtime.calls WHERE grant_id=gid AND state<>'succeeded')
 OR (SELECT count(*) FROM model_runtime.calls WHERE grant_id=gid)<>g.max_requests
 OR g.used_requests<>g.max_requests THEN RAISE EXCEPTION 'grant incomplete'; END IF;
 UPDATE model_runtime.grants SET state='completed',updated_at=now() WHERE id=gid;
END $$;
REVOKE ALL ON FUNCTION model_runtime.prepare_live_call(uuid,text,text,jsonb),model_runtime.consume_live_slot(uuid,text),
 model_runtime.stop_grant(uuid,text),model_runtime.complete_grant(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION model_runtime.prepare_live_call(uuid,text,text,jsonb),model_runtime.consume_live_slot(uuid,text),
 model_runtime.stop_grant(uuid,text),model_runtime.complete_grant(uuid) TO scholar_runtime;
