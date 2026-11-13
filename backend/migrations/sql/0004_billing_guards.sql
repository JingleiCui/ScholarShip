CREATE OR REPLACE FUNCTION billing.reserve_call(sid uuid, intent text, provider_name text, operation_name text,
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
   OR jsonb_typeof(price) IS DISTINCT FROM 'object' OR price->>'currency' IS DISTINCT FROM 'CNY' OR offline_mode IS NULL
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

CREATE OR REPLACE FUNCTION billing.mark_sent(cid uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=billing,scholar,pg_temp AS $$
DECLARE uid uuid; h billing.allocations%ROWTYPE; p billing.pools%ROWTYPE;
BEGIN
 uid=billing.current_owner(true);
 SELECT * INTO p FROM billing.pools WHERE id=(SELECT pool_id FROM billing.allocations WHERE call_id=cid AND owner_id=uid) FOR UPDATE;
 SELECT * INTO h FROM billing.allocations WHERE call_id=cid AND owner_id=uid FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'call not owned' USING ERRCODE='42501'; END IF;
 IF h.state<>'held' THEN RETURN false; END IF;
 IF NOT p.active OR now()<p.period_start OR now()>=p.period_end OR p.blocked OR (NOT h.offline AND NOT p.live_enabled) THEN RAISE EXCEPTION 'billing gate closed'; END IF;
 UPDATE billing.allocations SET state='sent' WHERE call_id=cid;
 UPDATE scholar.external_calls SET status='sent',started_at=now(),updated_at=now() WHERE id=cid;
 RETURN true;
END $$;