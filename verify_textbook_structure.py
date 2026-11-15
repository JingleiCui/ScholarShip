"""LOCAL-only 541-page text-layer acceptance; never uploads the book or calls OCR/models."""

from pathlib import Path
import json
import time
import httpx
from dotenv import dotenv_values

# USER CONFIGURATION
ROOT = Path(__file__).resolve().parent
ACTION = "status"  # status / run
API = "http://127.0.0.1:18080/api/v1/"
REFERENCE = ROOT / "var/reports/live-business-import.json"
REPORT = ROOT / "var/reports/textbook-structure.json"
TITLE_CONTAINS = "UnderstandingDeepLearning"
EXPECTED_PAGES = 541
TIMEOUT_SECONDS = 1800


def main():
    if ACTION == "status":
        print(REPORT.read_text() if REPORT.exists() else '{"verified":false}')
        return
    if ACTION != "run":
        raise ValueError("unknown_action")
    ref = json.loads(REFERENCE.read_text())
    with httpx.Client(base_url=API, headers={"Origin": "http://127.0.0.1:15173"}, timeout=60) as client:
        deadline = time.monotonic() + 30
        while True:
            try:
                if client.get("health/ready").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("local_api_not_ready")
            time.sleep(0.5)

        def api(method, path, **kwargs):
            r = client.request(method, path, **kwargs)
            if r.status_code >= 400:
                raise RuntimeError("textbook_http_" + str(r.status_code))
            return r.json()

        who = api(
            "POST",
            "auth/login",
            json={"username": "researcher", "password": dotenv_values(ROOT / ".env")["INITIAL_USER_PASSWORD"]},
        )
        if who["id"] != ref["owner_id"]:
            raise RuntimeError("owner_mismatch")
        client.headers["X-CSRF-Token"] = client.cookies["ss_csrf"]
        papers = api("GET", f"projects/{ref['project_id']}/papers")
        matching = [p for p in papers if TITLE_CONTAINS in p["title"]]
        if len(matching) != 1:
            raise ValueError("unique_textbook_required")
        version = matching[0]["version_id"]
        job = api(
            "POST",
            f"studio/projects/{ref['project_id']}/tasks",
            json={"operation": "local_parse", "version_id": version},
            headers={"Idempotency-Key": "textbook-structure-v3-" + version},
        )
        start = time.monotonic()
        deadline = start + TIMEOUT_SECONDS
        report = {
            "run_id": job["id"],
            "source_version_id": version,
            "new_model_requests": 0,
            "new_uploads": 0,
            "ocr_performed": False,
            "expected_pages": EXPECTED_PAGES,
            "passed": False,
        }
        while True:
            row = api("GET", "runs/" + job["id"])
            report["status"] = row["status"]
            report["elapsed_seconds"] = round(time.monotonic() - start, 2)
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            if row["status"] not in {"queued", "running"}:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("textbook_task_timeout_no_resend")
            time.sleep(2)
        report["result"] = row["result_json"]
        report["error"] = row["error_json"]
        if row["status"] == "succeeded":
            pid = row["result_json"]["parse_id"]
            report["sample_pages"] = []
            for n in [1, EXPECTED_PAGES // 2, EXPECTED_PAGES]:
                sample = api("GET", f"parses/{pid}/pages/{n}")
                report["sample_pages"].append(
                    {
                        "page": n,
                        "blocks": len(sample["blocks"]),
                        "text_chars": sum(len(b["canonical_text"]) for b in sample["blocks"]),
                    }
                )
            report["passed"] = row["result_json"].get("pages") == EXPECTED_PAGES
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "passed": report["passed"],
                    "status": report["status"],
                    "seconds": report["elapsed_seconds"],
                    "new_model_requests": 0,
                    "ocr_performed": False,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
