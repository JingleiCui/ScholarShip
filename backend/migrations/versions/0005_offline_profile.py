"""Install an explicitly synthetic profile without changing the live profile."""

import hashlib
import json
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_offline_profile"
down_revision = "0004_billing_guards"
branch_labels = None
depends_on = None

# Frozen migration snapshot. Runtime profile changes require a new revision.
PROFILE = {
    "mode": "synthetic",
    "provider": "offline",
    "model": "hash-bow-sha256-v1",
    "dimensions": 1024,
    "metric": "cosine",
    "tokenizer": "utf8-bytes-v1",
    "target_tokens": 600,
    "max_tokens": 900,
    "overlap_tokens": 80,
    "chunker": "block-unicode-boundary-v1",
    "lexical": "nfkc-word-cjk-bigram-v1",
    "document_prefix": "",
    "query_prefix": "",
    "asymmetric": False,
    "normalization": "original-unmodified",
    "rrf_k": 60,
}


def upgrade():
    text = json.dumps(PROFILE, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    statement = sa.text("""INSERT INTO scholar.index_profiles
      (profile_key,provider,model_name,dimensions,metric,chunker_revision,lexical_revision,config_json,fingerprint)
      VALUES ('offline-hash-1024-v1','offline','hash-bow-sha256-v1',1024,'cosine',
      'block-unicode-boundary-v1','nfkc-word-cjk-bigram-v1',:config,:hash)""").bindparams(
        sa.bindparam("config", type_=JSONB)
    )
    op.get_bind().execute(statement, {"config": PROFILE, "hash": hashlib.sha256(text.encode()).hexdigest()})
    op.get_bind().connection.driver_connection.execute("""
    CREATE OR REPLACE FUNCTION scholar.claim_outbox_v2(batch_size integer)
    RETURNS TABLE(id uuid,owner_id uuid,project_id uuid,run_id uuid,lease_token uuid,command text)
    LANGUAGE sql SECURITY DEFINER SET search_path=scholar,pg_temp AS $$
      WITH picked AS (
        SELECT o.id FROM scholar.task_outbox o JOIN scholar.runs r ON r.id=o.run_id
        WHERE o.command IN ('system_probe','preview_parse','offline_build','offline_qa') AND o.available_at<=now()
          AND (o.lease_until IS NULL OR o.lease_until<now())
          AND (r.status='queued' OR (r.status='running' AND r.lease_until<now()))
        ORDER BY o.available_at,o.id LIMIT least(greatest(batch_size,1),20) FOR UPDATE OF o SKIP LOCKED
      ) UPDATE scholar.task_outbox o SET lease_token=gen_random_uuid(),lease_until=now()+interval '30 seconds',
        delivery_attempts=o.delivery_attempts+1,updated_at=now() FROM picked WHERE o.id=picked.id
        RETURNING o.id,o.owner_id,o.project_id,o.run_id,o.lease_token,o.command;
    $$;
    REVOKE ALL ON FUNCTION scholar.claim_outbox_v2(integer) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION scholar.claim_outbox_v2(integer) TO scholar_runtime;
    """)


def downgrade():
    raise RuntimeError("Do not remove profiles referenced by immutable generations.")
