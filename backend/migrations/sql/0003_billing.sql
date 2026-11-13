CREATE SCHEMA billing;
REVOKE ALL ON SCHEMA billing FROM PUBLIC;
GRANT USAGE ON SCHEMA billing TO scholar_runtime;
CREATE TABLE billing.pools (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 active boolean NOT NULL DEFAULT true,
 period_start timestamptz NOT NULL DEFAULT now(),
 period_end timestamptz NOT NULL DEFAULT now()+interval '30 days',
 limit_amount numeric(18,6) NOT NULL DEFAULT 100 CHECK(limit_amount>=0),
 spent_amount numeric(18,6) NOT NULL DEFAULT 0 CHECK(spent_amount>=0),
 reserved_amount numeric(18,6) NOT NULL DEFAULT 0 CHECK(reserved_amount>=0),
 live_enabled boolean NOT NULL DEFAULT false,
 blocked boolean NOT NULL DEFAULT false,
 CHECK(period_end>period_start)
);
CREATE UNIQUE INDEX one_active_pool ON billing.pools(active) WHERE active;
INSERT INTO billing.pools DEFAULT VALUES;
CREATE TABLE billing.allocations (
 call_id uuid PRIMARY KEY REFERENCES scholar.external_calls(id) ON DELETE RESTRICT,
 reservation_id uuid NOT NULL UNIQUE REFERENCES scholar.budget_reservations(id) ON DELETE RESTRICT,
 owner_id uuid NOT NULL REFERENCES scholar.users(id) ON DELETE RESTRICT,
 pool_id uuid NOT NULL REFERENCES billing.pools(id) ON DELETE RESTRICT,
 offline boolean NOT NULL,
 state text NOT NULL CHECK(state IN ('held','sent','unknown','settled','released')),
 amount numeric(18,6) NOT NULL CHECK(amount>0),
 actual numeric(18,6),
 price_snapshot jsonb NOT NULL,
 receipt_sha256 text,
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK(actual IS NULL OR actual>=0)
);
ALTER TABLE billing.allocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing.allocations FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_only ON billing.allocations TO scholar_runtime USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid);
GRANT SELECT ON billing.pools,billing.allocations TO scholar_runtime;
REVOKE INSERT,UPDATE,DELETE ON scholar.external_calls,scholar.budget_reservations,scholar.usage_ledger FROM scholar_runtime;

CREATE FUNCTION billing.current_owner(require_active boolean DEFAULT false) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=billing,scholar,pg_temp AS $$
DECLARE uid uuid; u scholar.users%ROWTYPE;
BEGIN
 uid=nullif(current_setting('app.owner_id',true),'')::uuid;
 SELECT * INTO u FROM scholar.users WHERE id=uid;
 IF NOT FOUND OR (require_active AND (u.disabled_at IS NOT NULL OR u.role<>'researcher')) THEN
   RAISE EXCEPTION 'billing actor not allowed' USING ERRCODE='42501';
 END IF;
 RETURN uid;
END $$;

CREATE FUNCTION billing.reserve_call(sid uuid, intent text, provider_name text, operation_name text,
 request_hash text, estimate numeric, price jsonb, offline_mode boolean DEFAULT true) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=billing,scholar,pg_temp AS $$
DECLARE uid uuid; p billing.pools%ROWTYPE; a scholar.budget_accounts%ROWTYPE;
 s scholar.run_steps%ROWTYPE; old scholar.external_calls%ROWTYPE; h billing.allocations%ROWTYPE;
 cid uuid; rid uuid;
BEGIN
 uid=billing.current_owner(true);
 IF estimate IS NULL OR estimate::text IN ('NaN','Infinity','-Infinity') OR estimate<0.000001
   OR intent IS NULL OR length(intent) NOT BETWEEN 1 AND 128
   OR request_hash IS NULL OR request_hash !~ '^[0-9a-f]{64}$'
   OR jsonb_typeof(price) IS DISTINCT FROM 'object' OR offline_mode IS NULL
   OR provider_name IS NULL OR operation_name IS NULL THEN
   RAISE EXCEPTION 'invalid billing intent' USING ERRCODE='23514';
 END IF;
 SELECT * INTO p FROM billing.pools WHERE active FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'no active billing pool'; END IF;
 SELECT * INTO s FROM scholar.run_steps WHERE id=sid AND owner_id=uid;
 IF NOT FOUND THEN RAISE EXCEPTION 'step not owned' USING ERRCODE='42501'; END IF;
 SELECT * INTO old FROM scholar.external_calls WHERE owner_id=uid AND project_id=s.project_id
   AND provider=provider_name AND intent_key=intent;
 IF FOUND THEN
   SELECT * INTO h FROM billing.allocations WHERE call_id=old.id;
   IF NOT FOUND OR old.step_id<>sid OR old.operation<>operation_name OR old.request_sha256<>request_hash
     OR h.amount<>estimate OR h.price_snapshot<>price OR h.offline<>offline_mode THEN
     RAISE EXCEPTION 'idempotency conflict' USING ERRCODE='23514';
   END IF;
   RETURN old.id;
 END IF;
 IF p.blocked OR now()<p.period_start OR now()>=p.period_end OR (NOT offline_mode AND NOT p.live_enabled) THEN
   RAISE EXCEPTION 'billing gate closed' USING ERRCODE='42501';
 END IF;
 IF s.status NOT IN ('pending','running') THEN RAISE EXCEPTION 'step not executable'; END IF;
 SELECT * INTO a FROM scholar.budget_accounts WHERE owner_id=uid AND category='ai' AND currency='CNY'
   AND period_start<=now() AND period_end>now() FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'no active account budget'; END IF;
 IF p.spent_amount+p.reserved_amount+estimate>p.limit_amount OR a.spent_amount+a.reserved_amount+estimate>a.limit_amount THEN
   RAISE EXCEPTION 'budget exhausted' USING ERRCODE='23514';
 END IF;
 INSERT INTO scholar.external_calls(owner_id,project_id,step_id,provider,operation,intent_key,request_sha256,status)
   VALUES(uid,s.project_id,sid,provider_name,operation_name,intent,request_hash,'prepared') RETURNING id INTO cid;
 INSERT INTO scholar.budget_reservations(owner_id,project_id,account_id,call_id,reserved_amount,state)
   VALUES(uid,s.project_id,a.id,cid,estimate,'held') RETURNING id INTO rid;
 INSERT INTO billing.allocations(call_id,reservation_id,owner_id,pool_id,offline,state,amount,price_snapshot)
   VALUES(cid,rid,uid,p.id,offline_mode,'held',estimate,price);
 UPDATE billing.pools SET reserved_amount=reserved_amount+estimate WHERE id=p.id;
 UPDATE scholar.budget_accounts SET reserved_amount=reserved_amount+estimate,updated_at=now() WHERE id=a.id;
 INSERT INTO scholar.audit_events(owner_id,project_id,action,subject_json,safe_payload)
   VALUES(uid,s.project_id,'billing_reserved',jsonb_build_object('call_id',cid),jsonb_build_object('offline',offline_mode,'amount',estimate));
 RETURN cid;
END $$;

CREATE FUNCTION billing.mark_sent(cid uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=billing,scholar,pg_temp AS $$
DECLARE uid uuid; h billing.allocations%ROWTYPE; p billing.pools%ROWTYPE;
BEGIN
 uid=billing.current_owner(true);
 SELECT * INTO p FROM billing.pools WHERE id=(SELECT pool_id FROM billing.allocations WHERE call_id=cid AND owner_id=uid) FOR UPDATE;
 SELECT * INTO h FROM billing.allocations WHERE call_id=cid AND owner_id=uid FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'call not owned' USING ERRCODE='42501'; END IF;
 IF h.state<>'held' THEN RETURN false; END IF;
 IF p.blocked OR (NOT h.offline AND NOT p.live_enabled) THEN RAISE EXCEPTION 'billing gate closed'; END IF;
 UPDATE billing.allocations SET state='sent' WHERE call_id=cid;
 UPDATE scholar.external_calls SET status='sent',started_at=now(),updated_at=now() WHERE id=cid;
 RETURN true;
END $$;

CREATE FUNCTION billing.mark_unknown(cid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=billing,scholar,pg_temp AS $$
DECLARE uid uuid; h billing.allocations%ROWTYPE;
BEGIN
 uid=billing.current_owner();
 PERFORM 1 FROM billing.pools WHERE id=(SELECT pool_id FROM billing.allocations WHERE call_id=cid AND owner_id=uid) FOR UPDATE;
 SELECT * INTO h FROM billing.allocations WHERE call_id=cid AND owner_id=uid FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'call not owned' USING ERRCODE='42501'; END IF;
 IF h.state='unknown' THEN RETURN; END IF;
 IF h.state<>'sent' THEN RAISE EXCEPTION 'invalid unknown transition'; END IF;
 UPDATE billing.allocations SET state='unknown' WHERE call_id=cid;
 UPDATE scholar.external_calls SET status='unknown',updated_at=now() WHERE id=cid;
 UPDATE scholar.budget_reservations SET state='unknown',updated_at=now() WHERE id=h.reservation_id;
END $$;

CREATE FUNCTION billing.settle_call(cid uuid, actual_cost numeric, receipt jsonb, receipt_hash text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=billing,scholar,pg_temp AS $$
DECLARE uid uuid; h billing.allocations%ROWTYPE; r scholar.budget_reservations%ROWTYPE; c scholar.external_calls%ROWTYPE;
BEGIN
 uid=billing.current_owner();
 IF actual_cost IS NULL OR actual_cost::text IN ('NaN','Infinity','-Infinity') OR actual_cost<0
   OR jsonb_typeof(receipt) IS DISTINCT FROM 'object' OR receipt_hash !~ '^[0-9a-f]{64}$' OR receipt_hash IS NULL THEN
   RAISE EXCEPTION 'invalid settlement' USING ERRCODE='23514';
 END IF;
 PERFORM 1 FROM billing.pools WHERE id=(SELECT pool_id FROM billing.allocations WHERE call_id=cid AND owner_id=uid) FOR UPDATE;
 SELECT * INTO h FROM billing.allocations WHERE call_id=cid AND owner_id=uid FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'call not owned' USING ERRCODE='42501'; END IF;
 IF h.state='settled' THEN
   IF h.actual<>actual_cost OR h.receipt_sha256<>receipt_hash THEN RAISE EXCEPTION 'conflicting settlement'; END IF;
   RETURN;
 END IF;
 IF h.state NOT IN ('sent','unknown') THEN RAISE EXCEPTION 'unstarted call cannot settle'; END IF;
 SELECT * INTO r FROM scholar.budget_reservations WHERE id=h.reservation_id;
 SELECT * INTO c FROM scholar.external_calls WHERE id=cid;
 PERFORM 1 FROM scholar.budget_accounts WHERE id=r.account_id FOR UPDATE;
 UPDATE billing.pools SET reserved_amount=reserved_amount-h.amount,spent_amount=spent_amount+actual_cost,
   blocked=blocked OR spent_amount+actual_cost+reserved_amount-h.amount>limit_amount WHERE id=h.pool_id;
 UPDATE scholar.budget_accounts SET reserved_amount=reserved_amount-h.amount,spent_amount=spent_amount+actual_cost,updated_at=now() WHERE id=r.account_id;
 UPDATE billing.allocations SET state='settled',actual=actual_cost,receipt_sha256=receipt_hash WHERE call_id=cid;
 UPDATE scholar.budget_reservations SET state='settled',settled_at=now(),updated_at=now() WHERE id=h.reservation_id;
 UPDATE scholar.external_calls SET status='succeeded',receipt_json=receipt,finished_at=now(),updated_at=now() WHERE id=cid;
 INSERT INTO scholar.usage_ledger(owner_id,project_id,account_id,call_id,reservation_id,entry_key,delta_amount,basis,price_snapshot,usage_json)
   VALUES(uid,c.project_id,r.account_id,cid,r.id,'settle:'||cid,actual_cost,'usage_estimate',h.price_snapshot,
     jsonb_build_object('offline',h.offline,'provider_usage',receipt->'usage'));
 INSERT INTO scholar.audit_events(owner_id,project_id,action,subject_json,safe_payload)
   VALUES(uid,c.project_id,'billing_settled',jsonb_build_object('call_id',cid),jsonb_build_object('actual',actual_cost,'offline',h.offline));
END $$;

CREATE FUNCTION billing.release_unsent(cid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=billing,scholar,pg_temp AS $$
DECLARE uid uuid; h billing.allocations%ROWTYPE; rid uuid;
BEGIN
 uid=billing.current_owner();
 PERFORM 1 FROM billing.pools WHERE id=(SELECT pool_id FROM billing.allocations WHERE call_id=cid AND owner_id=uid) FOR UPDATE;
 SELECT * INTO h FROM billing.allocations WHERE call_id=cid AND owner_id=uid FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'call not owned' USING ERRCODE='42501'; END IF;
 IF h.state='released' THEN RETURN; END IF;
 IF h.state<>'held' THEN RAISE EXCEPTION 'sent or unknown call cannot release'; END IF;
 SELECT account_id INTO rid FROM scholar.budget_reservations WHERE id=h.reservation_id;
 PERFORM 1 FROM scholar.budget_accounts WHERE id=rid FOR UPDATE;
 UPDATE billing.pools SET reserved_amount=reserved_amount-h.amount WHERE id=h.pool_id;
 UPDATE scholar.budget_accounts SET reserved_amount=reserved_amount-h.amount,updated_at=now() WHERE id=rid;
 UPDATE billing.allocations SET state='released' WHERE call_id=cid;
 UPDATE scholar.budget_reservations SET state='released',settled_at=now(),updated_at=now() WHERE id=h.reservation_id;
 UPDATE scholar.external_calls SET status='cancelled',finished_at=now(),updated_at=now() WHERE id=cid;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA billing FROM PUBLIC;
GRANT EXECUTE ON FUNCTION billing.reserve_call(uuid,text,text,text,text,numeric,jsonb,boolean),billing.mark_sent(uuid),billing.mark_unknown(uuid),billing.settle_call(uuid,numeric,jsonb,text),billing.release_unsent(uuid) TO scholar_runtime;
