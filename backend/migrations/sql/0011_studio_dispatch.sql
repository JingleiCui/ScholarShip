CREATE OR REPLACE FUNCTION scholar.claim_outbox_v2(batch_size integer)
RETURNS TABLE(id uuid,owner_id uuid,project_id uuid,run_id uuid,lease_token uuid,command text)
LANGUAGE sql SECURITY DEFINER SET search_path=scholar,pg_temp AS $$
WITH picked AS (
 SELECT o.id FROM scholar.task_outbox o JOIN scholar.runs r ON r.id=o.run_id
 WHERE o.command IN ('system_probe','preview_parse','offline_build','offline_qa','research_build','research_qa','research_studio') AND o.available_at<=now()
 AND (o.lease_until IS NULL OR o.lease_until<now()) AND (r.status='queued' OR (r.status='running' AND r.lease_until<now()))
 ORDER BY o.available_at,o.id LIMIT least(greatest(batch_size,1),20) FOR UPDATE OF o SKIP LOCKED
) UPDATE scholar.task_outbox o SET lease_token=gen_random_uuid(),lease_until=now()+interval '30 seconds',delivery_attempts=o.delivery_attempts+1,updated_at=now() FROM picked WHERE o.id=picked.id
RETURNING o.id,o.owner_id,o.project_id,o.run_id,o.lease_token,o.command;
$$;

CREATE FUNCTION scholar.due_metadata_subscriptions(batch_size integer)
RETURNS TABLE(id uuid,owner_id uuid,project_id uuid) LANGUAGE sql SECURITY DEFINER
SET search_path=scholar,pg_temp AS $$
SELECT id,owner_id,project_id FROM scholar.subscriptions WHERE enabled AND next_due_at<=now()
 AND (lease_until IS NULL OR lease_until<now()) ORDER BY next_due_at,id LIMIT least(greatest(batch_size,1),10);
$$;
REVOKE ALL ON FUNCTION scholar.due_metadata_subscriptions(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scholar.due_metadata_subscriptions(integer) TO scholar_runtime;
