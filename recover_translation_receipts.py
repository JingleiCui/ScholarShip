"""LOCAL-only reconciliation of captured successful partial translation receipts."""

from pathlib import Path
from uuid import UUID
import json

# USER CONFIGURATION — this entry never sends a model or changes a failed call/run.
ROOT = Path(__file__).resolve().parent
ACTION = "status"  # status / recover
REFERENCE = ROOT / "var/reports/live-business-import.json"
BATCH_REPORT = ROOT / "var/reports/cloud-business-acceptance.json"
OUTPUT = ROOT / "var/reports/translation-part-recovery.json"


def main():
    from scholarship.domains.studio.translation_parts import recover_failed_parts, prepare
    from scholarship.models.configuration import load_selection

    ref = json.loads(REFERENCE.read_text())
    owner, project = UUID(ref["owner_id"]), UUID(ref["project_id"])
    result = {"new_requests": 0}
    if ACTION == "recover":
        batch = json.loads(BATCH_REPORT.read_text())
        if batch["source_parse_id"] != ref["parse_id"]:
            raise ValueError("source_scope_changed")
        failed = [s for s in batch["steps"] if s["label"] == "translate" and s["status"] == "failed"]
        if not failed:
            raise ValueError("no_failed_translation")
        result.update(recover_failed_parts(owner, project, UUID(failed[-1]["run_id"])))
    elif ACTION != "status":
        raise ValueError("unknown_action")
    plan = prepare(owner, project, {"parse_id": ref["parse_id"]}, load_selection("free_api"), False)
    result.update(
        validated_segments=len(plan["reused"]),
        validated_plaintext_parts=len(plan["cached"]),
        remaining_requests=len(plan["packets"]),
        packet_sizes=[len(p) for p in plan["packets"]],
    )
    if ACTION == "recover":
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
