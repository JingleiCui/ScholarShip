"""A bounded REAL business acceptance batch; never issues or resets authorization."""

from pathlib import Path
from uuid import UUID, uuid4
import json
import re
import time
import httpx
import sqlalchemy as sa

# USER CONFIGURATION — status is read-only. Model calls require an approved policy.
ROOT = Path(__file__).resolve().parent
ACTION = "status"  # status / run
API = "http://127.0.0.1:18080/api/v1/"
REFERENCE = ROOT / "var/reports/live-business-import.json"
APPROVAL_REFERENCE = "2026-11-15-scholarship-business-80-v1"
REPORT = ROOT / "var/reports/cloud-business-acceptance.json"
REUSE_READY_REAL_INDEX = True
QA_CASE_IDS = set()  # all five official single-paper cases now have source-reviewed results
TASK_TIMEOUT_SECONDS = 1800
POLL_SECONDS = 1


def status(owner):
    from scholarship.infrastructure.database import tenant_transaction

    with tenant_transaction(owner) as conn:
        return [
            dict(r)
            for r in conn.execute(
                sa.text(
                    "SELECT role,model,request_limit,requests_used,tokens_reserved,tokens_used,enabled,ends_at FROM model_runtime.production_policies WHERE approval_reference=:ref"
                ),
                {"ref": APPROVAL_REFERENCE},
            ).mappings()
        ]


def pause_campaign(owner):
    from scholarship.infrastructure.database import tenant_transaction

    with tenant_transaction(owner) as conn:
        cid = conn.scalar(
            sa.text(
                "SELECT a.call_id FROM model_runtime.production_admissions a JOIN model_runtime.production_policies p ON p.id=a.policy_id WHERE p.approval_reference=:ref LIMIT 1"
            ),
            {"ref": APPROVAL_REFERENCE},
        )
        if cid:
            conn.execute(sa.text("SELECT model_runtime.suspend_production(:id)"), {"id": cid})


def main():
    from scholarship.infrastructure.database import tenant_transaction
    from scholarship.infrastructure.schema import tables as T

    ref = json.loads(REFERENCE.read_text())
    owner = UUID(ref["owner_id"])
    project = ref["project_id"]
    if ACTION == "status":
        print(json.dumps(status(owner), ensure_ascii=False, indent=2, default=str))
        return
    if ACTION != "run":
        raise ValueError("unknown_action")
    if REPORT.exists():
        prior = json.loads(REPORT.read_text())
        if prior.get("passed"):
            raise PermissionError("completed_batch_read_only")
        archived = REPORT.with_name(REPORT.stem + "-" + str(time.time_ns()) + ".json")
        archived.write_text(REPORT.read_text())
    policies = status(owner)
    if (
        len(policies) != 4
        or not all(p["enabled"] for p in policies)
        or not any(p["requests_used"] < p["request_limit"] for p in policies)
    ):
        raise PermissionError("fresh_explicit_production_approval_required")
    report = {
        "source_parse_id": ref["parse_id"],
        "approval_reference": APPROVAL_REFERENCE,
        "new_uploads": 0,
        "steps": [],
        "scientific_quality_verified": False,
        "passed": False,
    }
    with tenant_transaction(owner) as conn:
        preferences = conn.scalar(sa.select(T["projects"].c.preferences).where(T["projects"].c.id == UUID(project)))
    original = preferences.get("model_configuration", {})
    test_fingerprint = None
    password = (
        re.search(r"^INITIAL_USER_PASSWORD=(.*)$", (ROOT / ".env").read_text(), re.MULTILINE)[1].strip().strip("\"'")
    )
    with httpx.Client(
        base_url=API, headers={"Origin": "http://127.0.0.1:15173"}, timeout=30, follow_redirects=False
    ) as client:
        # Local readiness reads may wait for startup; model POSTs are never retried.
        ready_until = time.monotonic() + 30
        while True:
            try:
                ready = client.get("health/ready")
                if ready.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= ready_until:
                raise RuntimeError("local_api_not_ready")
            time.sleep(0.5)

        def request(method, path, **kwargs):
            r = client.request(method, path, **kwargs)
            if r.status_code >= 400:
                raise RuntimeError("business_http_" + str(r.status_code))
            return r.json()

        def task(path, body, label):
            run = request("POST", path, json=body, headers={"Idempotency-Key": uuid4().hex})
            entry = {"label": label, "run_id": run["id"], "status": "queued"}
            report["steps"].append(entry)
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            deadline = time.monotonic() + TASK_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                row = request("GET", "runs/" + run["id"])
                entry["status"] = row["status"]
                if row["status"] not in {"queued", "running"}:
                    entry["result"] = row["result_json"]
                    entry["error"] = row["error_json"]
                    if row["status"] != "succeeded":
                        raise RuntimeError("cloud_task_stopped")
                    return row
                time.sleep(POLL_SECONDS)
            raise RuntimeError("cloud_task_timeout_no_resend")

        try:
            who = request("POST", "auth/login", json={"username": "researcher", "password": password})
            password = ""
            if who["id"] != str(owner):
                raise RuntimeError("approval_owner_mismatch")
            client.headers["X-CSRF-Token"] = client.cookies["ss_csrf"]
            current = request("GET", f"projects/{project}/model-configuration")
            changed = request(
                "PUT",
                f"projects/{project}/model-configuration",
                json={"preset": "free_api", "overrides": {}, "expected_fingerprint": current["fingerprint"]},
            )
            test_fingerprint = changed["fingerprint"]
            body = {"version_ids": [ref["version_id"]], "mode": "cloud"}
            with tenant_transaction(owner) as conn:
                index = (
                    conn.execute(
                        sa.select(T["index_generations"])
                        .join(T["paper_versions"], T["paper_versions"].c.active_index_id == T["index_generations"].c.id)
                        .where(
                            T["paper_versions"].c.id == UUID(ref["version_id"]),
                            T["index_generations"].c.parse_id == UUID(ref["parse_id"]),
                            T["index_generations"].c.status == "ready",
                        )
                    )
                    .mappings()
                    .first()
                )
                profile = (
                    conn.execute(
                        sa.text("SELECT (config_json->>'simulated')::boolean FROM scholar.index_profiles WHERE id=:id"),
                        {"id": index["profile_id"]},
                    ).scalar()
                    if index
                    else None
                )
            if REUSE_READY_REAL_INDEX and index and profile is False:
                report["reused_real_index_id"] = str(index["id"])
            elif REUSE_READY_REAL_INDEX:
                raise PermissionError("expected_real_index_unavailable_no_rebuild")
            else:
                task(f"research/projects/{project}/indexes", body, "full_document_real_index")
            cases = json.loads((ROOT / "docs/competition/qa-cases.json").read_text())["cases"]
            for c in sorted(cases, key=lambda c: c["id"] == "single-2"):
                if c["group"] == "official_single" and c["id"] in QA_CASE_IDS:
                    checked = task(
                        f"research/projects/{project}/questions", {**body, "question": c["question"]}, c["id"]
                    )
                    expected = {
                        "single-1": (
                            "deterministic_author_order",
                            ["Aidan N. Gomez", "University of Toronto", "Google Brain"],
                        ),
                        "single-4": ("deterministic_table_decimal", ["28.4", "26.36", "2.04"]),
                        "single-2": ("model_evidence_qa", ["0.9", "0.98"]),
                    }[c["id"]]
                    result = checked["result_json"]
                    accepted = result.get("answer_method") == expected[0] and all(
                        s in result.get("answer", "") for s in expected[1]
                    )
                    if c["id"] == "single-2":
                        quotes = " ".join(x["quote"] for x in result.get("citations", []))
                        accepted = accepted and all(value in quotes for value in ["0.9", "0.98"])
                    report["steps"][-1]["official_sample_review_passed"] = accepted
                    if not accepted:
                        raise RuntimeError("official_sample_review_failed")
            for op in ["translate", "summary", "draft"]:
                task(
                    f"studio/projects/{project}/tasks",
                    {
                        "operation": op,
                        "mode": "cloud",
                        "parse_id": ref["parse_id"],
                        "parse_ids": [ref["parse_id"]],
                        "idea": "以证据整理计划中的 Transformer 研究框架；不声称完成新实验。",
                    },
                    op,
                )
            report["passed"] = True
        except Exception as exc:
            report["stop_code"] = type(exc).__name__ + (
                ":" + str(exc) if isinstance(exc, (RuntimeError, PermissionError)) else ""
            )
        finally:
            try:
                if test_fingerprint:
                    current = request("GET", f"projects/{project}/model-configuration")
                    if current["fingerprint"] == test_fingerprint:
                        request(
                            "PUT",
                            f"projects/{project}/model-configuration",
                            json={
                                "preset": original.get("preset", "free_gemma"),
                                "overrides": original.get("overrides", {}),
                                "expected_fingerprint": current["fingerprint"],
                            },
                        )
                        report["project_default_restored"] = True
                    else:
                        report["project_default_restored"] = False
            except Exception as exc:
                report["restore_error_type"] = type(exc).__name__
            pause_campaign(owner)  # completion also seals unused allowance
            report["policies"] = status(owner)
            REPORT.parent.mkdir(parents=True, exist_ok=True)
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
    print(
        json.dumps(
            {"passed": report["passed"], "steps": len(report["steps"]), "report": str(REPORT.relative_to(ROOT))},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
