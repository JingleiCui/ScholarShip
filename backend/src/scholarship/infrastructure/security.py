"""Database-enforced tenant boundaries, provenance guards and narrow privileged APIs."""

import sqlalchemy as sa

from scholarship.infrastructure.schema import metadata


def install_security(conn, catalog):
    conn.exec_driver_sql("REVOKE ALL ON SCHEMA scholar FROM PUBLIC")
    conn.exec_driver_sql("GRANT USAGE ON SCHEMA scholar TO scholar_runtime, scholar_auth")
    for spec in catalog["tables"]:
        name = spec["name"]
        if name == "users":
            conn.exec_driver_sql("GRANT SELECT ON scholar.users TO scholar_auth")
            continue
        if spec["scope"] == "global":
            conn.exec_driver_sql(f"GRANT SELECT ON scholar.{name} TO scholar_runtime")
            continue
        conn.exec_driver_sql(f"ALTER TABLE scholar.{name} ENABLE ROW LEVEL SECURITY")
        conn.exec_driver_sql(f"ALTER TABLE scholar.{name} FORCE ROW LEVEL SECURITY")
        conn.exec_driver_sql(
            f"CREATE POLICY tenant_scope ON scholar.{name} TO scholar_runtime USING (owner_id = nullif(current_setting('app.owner_id', true), '')::uuid) WITH CHECK (owner_id = nullif(current_setting('app.owner_id', true), '')::uuid)"
        )
        if name == "auth_sessions":
            continue
        privileges = "SELECT, INSERT" if spec["write_mode"] == "append_only" else "SELECT, INSERT, UPDATE, DELETE"
        conn.exec_driver_sql(f"GRANT {privileges} ON scholar.{name} TO scholar_runtime")
    conn.exec_driver_sql("""
    CREATE FUNCTION scholar.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'immutable provenance row' USING ERRCODE='23514'; END $$;
    CREATE FUNCTION scholar.guard_identity() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF (to_jsonb(NEW)->'id') IS DISTINCT FROM (to_jsonb(OLD)->'id')
        OR (to_jsonb(NEW)->'owner_id') IS DISTINCT FROM (to_jsonb(OLD)->'owner_id')
        OR (to_jsonb(NEW)->'project_id') IS DISTINCT FROM (to_jsonb(OLD)->'project_id') THEN
        RAISE EXCEPTION 'scope identity is immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION scholar.guard_version() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' OR (to_jsonb(NEW) - ARRAY['active_parse_id','active_index_id','deleted_at','updated_at'])
        IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['active_parse_id','active_index_id','deleted_at','updated_at']) THEN
        RAISE EXCEPTION 'document carrier is immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION scholar.guard_publication() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.status='ready' THEN RAISE EXCEPTION 'published revision is immutable' USING ERRCODE='23514'; END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION scholar.guard_parse_child() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE pid uuid; state text;
    BEGIN
      IF TG_OP='UPDATE' AND OLD.parse_id IS DISTINCT FROM NEW.parse_id THEN
        RAISE EXCEPTION 'parse membership is immutable' USING ERRCODE='23514';
      END IF;
      IF TG_OP='DELETE' THEN pid=OLD.parse_id; ELSE pid=NEW.parse_id; END IF;
      SELECT status INTO state FROM scholar.document_parses WHERE id=pid FOR SHARE;
      IF state='ready' THEN RAISE EXCEPTION 'published parse content is immutable' USING ERRCODE='23514'; END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION scholar.guard_budget_period() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=scholar,pg_temp AS $$
    BEGIN
      PERFORM pg_advisory_xact_lock(hashtextextended(NEW.owner_id::text || ':' || NEW.category,0));
      IF EXISTS(SELECT 1 FROM scholar.budget_accounts b WHERE b.owner_id=NEW.owner_id
        AND b.category=NEW.category AND b.id<>NEW.id
        AND tstzrange(b.period_start,b.period_end,'[)') && tstzrange(NEW.period_start,NEW.period_end,'[)')) THEN
        RAISE EXCEPTION 'overlapping budget period' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER budget_period BEFORE INSERT OR UPDATE ON scholar.budget_accounts
      FOR EACH ROW EXECUTE FUNCTION scholar.guard_budget_period();
    CREATE TRIGGER immutable_carrier BEFORE UPDATE OR DELETE ON scholar.paper_versions
      FOR EACH ROW EXECUTE FUNCTION scholar.guard_version();
    """)
    for spec in catalog["tables"]:
        name = spec["name"]
        if "owner_id" in spec["columns"]:
            conn.exec_driver_sql(
                f"CREATE TRIGGER scope_identity BEFORE UPDATE ON scholar.{name} FOR EACH ROW EXECUTE FUNCTION scholar.guard_identity()"
            )
        if spec["write_mode"] == "append_only":
            conn.exec_driver_sql(
                f"CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON scholar.{name} FOR EACH ROW EXECUTE FUNCTION scholar.reject_mutation()"
            )
    for name in ("document_parses", "index_generations", "translation_revisions"):
        conn.exec_driver_sql(
            f"CREATE TRIGGER published_revision BEFORE UPDATE OR DELETE ON scholar.{name} FOR EACH ROW EXECUTE FUNCTION scholar.guard_publication()"
        )
    for name in ("document_pages", "document_blocks", "bibliographic_refs", "citation_mentions"):
        conn.exec_driver_sql(
            f"CREATE TRIGGER parse_content BEFORE INSERT OR UPDATE OR DELETE ON scholar.{name} FOR EACH ROW EXECUTE FUNCTION scholar.guard_parse_child()"
        )
    conn.exec_driver_sql("""
    CREATE FUNCTION scholar.session_create(uid uuid, token text, csrf text) RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path=scholar,pg_temp AS $$
      INSERT INTO scholar.auth_sessions(owner_id,token_hash,csrf_hash,expires_at)
      SELECT id,token,csrf,now()+interval '8 hours' FROM scholar.users WHERE id=uid AND disabled_at IS NULL;
    $$;
    CREATE FUNCTION scholar.session_lookup(token text)
    RETURNS TABLE(user_id uuid,username text,display_name text,role text,csrf_hash text)
    LANGUAGE sql SECURITY DEFINER SET search_path=scholar,pg_temp AS $$
      SELECT u.id,u.username,u.display_name,u.role,s.csrf_hash FROM scholar.auth_sessions s
      JOIN scholar.users u ON u.id=s.owner_id WHERE s.token_hash=token
      AND s.revoked_at IS NULL AND s.expires_at>now() AND u.disabled_at IS NULL;
    $$;
    CREATE FUNCTION scholar.session_revoke(token text) RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path=scholar,pg_temp AS $$
      UPDATE scholar.auth_sessions SET revoked_at=now() WHERE token_hash=token;
    $$;
    CREATE FUNCTION scholar.claim_outbox(batch_size integer)
    RETURNS TABLE(id uuid,owner_id uuid,project_id uuid,run_id uuid,lease_token uuid)
    LANGUAGE sql SECURITY DEFINER SET search_path=scholar,pg_temp AS $$
      WITH picked AS (
        SELECT o.id FROM scholar.task_outbox o JOIN scholar.runs r ON r.id=o.run_id
        WHERE o.command='system_probe' AND o.available_at<=now()
          AND (o.lease_until IS NULL OR o.lease_until<now())
          AND (r.status='queued' OR (r.status='running' AND r.lease_until<now()))
        ORDER BY o.available_at,o.id LIMIT least(greatest(batch_size,1),20) FOR UPDATE OF o SKIP LOCKED
      ) UPDATE scholar.task_outbox o SET lease_token=gen_random_uuid(),lease_until=now()+interval '30 seconds',
        delivery_attempts=o.delivery_attempts+1,updated_at=now() FROM picked WHERE o.id=picked.id
        RETURNING o.id,o.owner_id,o.project_id,o.run_id,o.lease_token;
    $$;
    REVOKE ALL ON ALL FUNCTIONS IN SCHEMA scholar FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION scholar.session_create(uuid,text,text),scholar.session_lookup(text),scholar.session_revoke(text) TO scholar_auth;
    GRANT EXECUTE ON FUNCTION scholar.claim_outbox(integer) TO scholar_runtime;
    """)


def assert_catalog(conn):
    """Checks the deployed catalog rather than assuming migration success."""
    count = conn.scalar(
        sa.text(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='scholar' AND table_name<>'alembic_version'"
        )
    )
    if count != len(metadata.tables):
        raise RuntimeError(f"Expected 41 business tables, found {count}")
