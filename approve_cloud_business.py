"""Explicit approval maintenance. Default plan is read-only and sends no models."""

from pathlib import Path
from uuid import UUID
from datetime import datetime, timedelta, timezone
import json
import math
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# USER CONFIGURATION — only set approval true after the user approves this exact plan.
ROOT = Path(__file__).resolve().parent
ACTION = "plan"  # plan / issue / resume / status; issue and resume are privileged maintenance
USER_APPROVED = False
RESUME_APPROVED = False  # separate confirmation after a batch stop; never resets quota/expiry
APPROVED_RESUME_LIMITS = None  # optional explicitly approved redistribution; sum must remain 80
APPROVED_PROPOSAL_SHA256 = ""
APPROVAL_REFERENCE = "2026-11-15-scholarship-business-80-v1"
PRESET = "free_api"  # ChatAnywhere gpt-4o-mini; OpenRouter Nemotron embeddings/rerank
REQUEST_LIMITS = {"embedding": 24, "rerank": 8, "chat": 32, "translation": 16}  # total 80, no reset
TOKEN_LIMIT_PER_ROLE = 1000000
VALID_HOURS = 24
REFERENCE = ROOT / "var/reports/live-business-import.json"
OUTPUT = ROOT / "var/reports/cloud-business-proposal.json"


def plan():
    from scholarship.models.configuration import load_selection
    from scholarship.domains.studio.translation import batches
    from scholarship.infrastructure.database import tenant_transaction
    from scholarship.infrastructure.schema import tables as T

    ref = json.loads(REFERENCE.read_text())
    owner, project, pid = (UUID(ref[k]) for k in ("owner_id", "project_id", "parse_id"))
    selection = load_selection(PRESET)
    with tenant_transaction(owner) as conn:
        rows = (
            conn.execute(
                sa.select(T["document_blocks"])
                .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
                .where(T["document_blocks"].c.parse_id == pid)
                .order_by(T["document_pages"].c.page_no, T["document_blocks"].c.ordinal)
            )
            .mappings()
            .all()
        )
        expected = (
            conn.scalar(
                sa.select(sa.func.max(T["index_generations"].c.expected_points)).where(
                    T["index_generations"].c.parse_id == pid
                )
            )
            or 0
        )
    groups = batches(rows)
    body = {
        "approval_reference": APPROVAL_REFERENCE,
        "owner_id": str(owner),
        "project_id": str(project),
        "allowed_parse_ids": [str(pid)],
        "source_scope": "only already-authorized 15-page scanned Transformer text; no new PDFs, CSVs or files sent",
        "preset": PRESET,
        "models": {
            role: {"provider": selection.roles[role].provider, "model": selection.roles[role].model}
            for role in REQUEST_LIMITS
        },
        "request_limits": REQUEST_LIMITS,
        "maximum_requests": sum(REQUEST_LIMITS.values()),
        "valid_hours": VALID_HOURS,
        "maximum_output_tokens": selection.max_output_tokens,
        "estimated_document_embedding_requests": 1 + math.ceil(max(0, expected - 1) / 10),
        "estimated_five_qa_requests": 15,
        "translation_batches": len(groups),
        "project_default_unchanged": True,
        "recharge": False,
        "automatic_fallback": False,
        "automatic_retry": False,
        "stop_on_429_or_unknown": True,
        "free_account_basis": "OpenRouter explicit :free IDs; ChatAnywhere free key user-attested, no recharge",
        "prior_validation_slots_unchanged": True,
        "snapshot": selection.snapshot(),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n")
    return body


def issue(proposal):
    if not USER_APPROVED:
        raise PermissionError("explicit_user_approval_required_for_this_exact_proposal")
    if (
        proposal["maximum_requests"] != 80
        or proposal["request_limits"] != REQUEST_LIMITS
        or len(proposal["allowed_parse_ids"]) != 1
    ):
        raise ValueError("proposal_scope_changed")
    from scholarship.domains.retrieval_profile import canonical, digest

    if not APPROVED_PROPOSAL_SHA256 or digest(canonical(proposal)) != APPROVED_PROPOSAL_SHA256:
        raise PermissionError("approved_proposal_hash_mismatch")
    # Administrator credentials are read only after the explicit approval gate.
    from scholarship.infrastructure.initialize import admin_engine
    from scholarship.infrastructure.schema import tables as T

    owner, project = UUID(proposal["owner_id"]), UUID(proposal["project_id"])
    engine = admin_engine()
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:ref,0))"), {"ref": APPROVAL_REFERENCE})
            old = (
                conn.execute(
                    sa.text(
                        "SELECT id,role,request_limit FROM model_runtime.production_policies WHERE approval_reference=:ref AND owner_id=:o AND project_id=:p"
                    ),
                    {"ref": APPROVAL_REFERENCE, "o": owner, "p": project},
                )
                .mappings()
                .all()
            )
            if old:
                if {r["role"]: r["request_limit"] for r in old} != REQUEST_LIMITS:
                    raise ValueError("existing_approval_conflict")
                return {"existing": True, "policies": len(old), "no_counter_reset": True}
            for role, limit in REQUEST_LIMITS.items():
                spec = proposal["models"][role]
                stmt = sa.text(
                    "INSERT INTO model_runtime.production_policies(owner_id,project_id,provider,model,role,enabled,free_verified,ends_at,request_limit,token_limit,approval_reference,data_scope_json) VALUES(:o,:p,:v,:m,:r,true,true,:end,:limit,:tokens,:ref,:scope)"
                ).bindparams(sa.bindparam("scope", type_=JSONB))
                conn.execute(
                    stmt,
                    {
                        "o": owner,
                        "p": project,
                        "v": spec["provider"],
                        "m": spec["model"],
                        "r": role,
                        "end": datetime.now(timezone.utc) + timedelta(hours=VALID_HOURS),
                        "limit": limit,
                        "tokens": TOKEN_LIMIT_PER_ROLE,
                        "ref": APPROVAL_REFERENCE,
                        "scope": {
                            "parse_ids": proposal["allowed_parse_ids"],
                            "account_free_basis": proposal["free_account_basis"],
                        },
                    },
                )
            conn.execute(sa.text("UPDATE billing.pools SET live_enabled=true WHERE active AND NOT blocked"))
            conn.execute(
                T["audit_events"]
                .insert()
                .values(
                    owner_id=owner,
                    project_id=project,
                    action="scoped_production_approval_issued",
                    subject_json={"approval_reference": APPROVAL_REFERENCE},
                    safe_payload={
                        "request_limits": REQUEST_LIMITS,
                        "allowed_parse_ids": proposal["allowed_parse_ids"],
                        "no_recharge": True,
                    },
                )
            )
    finally:
        engine.dispose()
    return {"issued": True, "maximum_requests": 80, "no_counter_reset": True}


def resume():
    if not RESUME_APPROVED:
        raise PermissionError("explicit_resume_confirmation_required")
    from scholarship.infrastructure.initialize import admin_engine
    from scholarship.infrastructure.schema import tables as T

    ref = json.loads(REFERENCE.read_text())
    owner, project, parse = (UUID(ref[k]) for k in ("owner_id", "project_id", "parse_id"))
    engine = admin_engine()
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:ref,0))"), {"ref": APPROVAL_REFERENCE})
            policies = (
                conn.execute(
                    sa.text(
                        "SELECT * FROM model_runtime.production_policies WHERE approval_reference=:ref AND owner_id=:o AND project_id=:p FOR UPDATE"
                    ),
                    {"ref": APPROVAL_REFERENCE, "o": owner, "p": project},
                )
                .mappings()
                .all()
            )
            previous_limits = {p["role"]: p["request_limit"] for p in policies}
            if len(policies) != 4 or set(previous_limits) != set(REQUEST_LIMITS) or sum(previous_limits.values()) != 80:
                raise ValueError("existing_approval_conflict")
            desired = APPROVED_RESUME_LIMITS or previous_limits
            if (
                set(desired) != set(REQUEST_LIMITS)
                or any(type(v) is not int or not 1 <= v <= 80 for v in desired.values())
                or sum(desired.values()) != 80
                or any(desired[p["role"]] < p["requests_used"] for p in policies)
            ):
                raise PermissionError("redistribution_must_preserve_total_and_consumed_requests")
            now = datetime.now(timezone.utc)
            if any(
                p["enabled"] or p["ends_at"] <= now or p["data_scope_json"].get("parse_ids") != [str(parse)]
                for p in policies
            ):
                raise PermissionError("resume_policy_not_eligible")
            if not any(p["requests_used"] < p["request_limit"] for p in policies):
                raise PermissionError("resume_allowance_exhausted")
            for p in policies:
                if desired[p["role"]] != p["request_limit"]:
                    conn.execute(
                        sa.text("UPDATE model_runtime.production_policies SET request_limit=:limit WHERE id=:id"),
                        {"id": p["id"], "limit": desired[p["role"]]},
                    )
            conn.execute(
                sa.text("UPDATE model_runtime.production_policies SET enabled=true WHERE id=ANY(:ids)"),
                {"ids": [p["id"] for p in policies]},
            )
            conn.execute(
                T["audit_events"]
                .insert()
                .values(
                    owner_id=owner,
                    project_id=project,
                    action="scoped_production_approval_resumed",
                    subject_json={"approval_reference": APPROVAL_REFERENCE},
                    safe_payload={
                        "prior_calls_preserved": True,
                        "total_limit_and_expiry_unchanged": True,
                        "previous_request_limits": previous_limits,
                        "new_request_limits": desired,
                        "counts": {p["role"]: p["requests_used"] for p in policies},
                    },
                )
            )
            return {
                "resumed": True,
                "remaining_requests": sum(desired[p["role"]] - p["requests_used"] for p in policies),
                "total_limit_and_expiry_unchanged": True,
            }
    finally:
        engine.dispose()


def main():
    if ACTION == "resume":
        print(json.dumps(resume(), ensure_ascii=False))
        return
    proposal = plan()
    if ACTION == "issue":
        result = issue(proposal)
    elif ACTION == "plan":
        result = {k: v for k, v in proposal.items() if k not in {"snapshot", "owner_id", "project_id"}}
    elif ACTION == "status":
        from scholarship.infrastructure.database import tenant_transaction

        with tenant_transaction(UUID(proposal["owner_id"])) as conn:
            result = [
                dict(r)
                for r in conn.execute(
                    sa.text(
                        "SELECT role,request_limit,requests_used,tokens_reserved,tokens_used,enabled,ends_at FROM model_runtime.production_policies WHERE approval_reference=:ref"
                    ),
                    {"ref": APPROVAL_REFERENCE},
                ).mappings()
            ]
    else:
        raise ValueError("unknown_action")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
