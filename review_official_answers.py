"""Record local source review of official samples; never overwrite historical answers."""

from pathlib import Path
from uuid import UUID
import json
import sqlalchemy as sa

# USER CONFIGURATION — explicit local review records, no new inference or uploads.
ROOT = Path(__file__).resolve().parent
ACTION = "status"  # status / record
REFERENCE = ROOT / "var/reports/live-business-import.json"
OUTPUT = ROOT / "var/reports/official-answer-review.json"


def main():
    from scholarship.infrastructure.database import tenant_transaction
    from scholarship.infrastructure.schema import tables as T

    ref = json.loads(REFERENCE.read_text())
    owner, project = UUID(ref["owner_id"]), UUID(ref["project_id"])
    records = {}
    for path in sorted((ROOT / "var/reports").glob("cloud-business-acceptance*.json")):
        batch = json.loads(path.read_text())
        if batch.get("source_parse_id") != ref["parse_id"]:
            continue
        for step in batch.get("steps", []):
            result = step.get("result") or {}
            answer = result.get("answer", "")
            label = step["label"]
            if step["status"] != "succeeded" or not label.startswith("single-") or not isinstance(answer, str):
                continue
            verdict = None
            reason = None
            if label == "single-1" and "Noam Shazeer" in answer:
                verdict = "incorrect"
                reason = "原文作者区中这是第二作者；该问题要求第六作者。"
            elif label == "single-1" and all(
                t in answer for t in ["Aidan N. Gomez", "University of Toronto", "Google Brain"]
            ):
                verdict = "sample_checked"
                reason = "已核对作者顺序、署名机构和首页工作地点脚注。"
            elif (
                label == "single-4"
                and result.get("answer_method") == "deterministic_table_decimal"
                and all(t in answer for t in ["28.4", "26.36", "2.04"])
            ):
                verdict = "sample_checked"
                reason = "已对照原件第 8 页表头、基准最大值及 Decimal 差值。"
            elif label == "single-4" and "26.3" in answer and "26.36" not in answer:
                verdict = "incorrect"
                reason = "选错此前最佳比较对象；原表最高 EN-DE 基准为 26.36，差值为 2.04。"
            elif label == "single-2" and all(t in answer for t in ["0.9", "0.98"]):
                verdict = "sample_checked"
                reason = "参数与冻结的 Adam 原文和逐字引文一致。"
            elif label == "single-3" and "8" in answer and "nvidia p100" in answer.casefold():
                verdict = "sample_checked"
                reason = "已核对原文的 GPU 数量和具体型号。"
            elif label == "single-5" and all(
                t in answer for t in ["Jimmy Lei Ba", "Jamie Ryan Kiros", "Geoffrey E Hinton", "2016"]
            ):
                verdict = "sample_checked"
                reason = "已核对 References 中 Layer Normalization 的作者与年份。"
            if verdict:
                records[step["run_id"]] = {
                    "case_id": label,
                    "verdict": verdict,
                    "reason": reason,
                    "review_scope": "official 15-page scan sample only",
                    "method": "local_source_review_and_structural_decimal_tool",
                }
    if ACTION == "record":
        with tenant_transaction(owner) as conn:
            for rid, review in records.items():
                row = (
                    conn.execute(
                        sa.select(T["runs"]).where(T["runs"].c.id == UUID(rid), T["runs"].c.project_id == project)
                    )
                    .mappings()
                    .one()
                )
                if ref["parse_id"] not in {p["parse_id"] for p in row["input_json"].get("parses", [])}:
                    raise ValueError("review_source_scope_mismatch")
                exists = conn.scalar(
                    sa.select(T["audit_events"].c.id).where(
                        T["audit_events"].c.project_id == project,
                        T["audit_events"].c.action == "research_answer_source_review",
                        T["audit_events"].c.subject_json["run_id"].astext == rid,
                        T["audit_events"].c.safe_payload["verdict"].astext == review["verdict"],
                    )
                )
                if not exists:
                    conn.execute(
                        T["audit_events"]
                        .insert()
                        .values(
                            owner_id=owner,
                            project_id=project,
                            action="research_answer_source_review",
                            subject_json={"run_id": rid, "parse_id": ref["parse_id"]},
                            safe_payload=review,
                        )
                    )
        OUTPUT.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    elif ACTION != "status":
        raise ValueError("unknown_action")
    print(
        json.dumps(
            {
                "reviews": len(records),
                "known_incorrect": sum(r["verdict"] == "incorrect" for r in records.values()),
                "checked_case_ids": sorted(
                    {r["case_id"] for r in records.values() if r["verdict"] == "sample_checked"}
                ),
                "old_results_unchanged": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
