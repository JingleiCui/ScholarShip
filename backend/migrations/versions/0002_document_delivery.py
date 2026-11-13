"""Enable durable PDF preview delivery without changing business tables."""

from alembic import op

revision = "0002_documents"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE FUNCTION scholar.claim_outbox_v2(batch_size integer)
    RETURNS TABLE(id uuid,owner_id uuid,project_id uuid,run_id uuid,lease_token uuid,command text)
    LANGUAGE sql SECURITY DEFINER SET search_path=scholar,pg_temp AS $$
      WITH picked AS (
        SELECT o.id FROM scholar.task_outbox o JOIN scholar.runs r ON r.id=o.run_id
        WHERE o.command IN ('system_probe','preview_parse') AND o.available_at<=now()
          AND (o.lease_until IS NULL OR o.lease_until<now())
          AND (r.status='queued' OR (r.status='running' AND r.lease_until<now()))
        ORDER BY o.available_at,o.id LIMIT least(greatest(batch_size,1),20) FOR UPDATE OF o SKIP LOCKED
      ) UPDATE scholar.task_outbox o SET lease_token=gen_random_uuid(),lease_until=now()+interval '30 seconds',
        delivery_attempts=o.delivery_attempts+1,updated_at=now() FROM picked WHERE o.id=picked.id
        RETURNING o.id,o.owner_id,o.project_id,o.run_id,o.lease_token,o.command;
    $$;
    REVOKE ALL ON FUNCTION scholar.claim_outbox_v2(integer) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION scholar.claim_outbox_v2(integer) TO scholar_runtime;""")


def downgrade():
    op.execute("DROP FUNCTION scholar.claim_outbox_v2(integer)")
