"""Run the three approved real-PDF acceptance samples without paid calls."""

from pathlib import Path
import hashlib
import json
import time
from uuid import uuid4
from dotenv import dotenv_values
import httpx

# USER CONFIGURATION
ROOT = Path(__file__).resolve().parent
API = "http://127.0.0.1:18080/api/v1"
TIMEOUT_SECONDS = 1200
SAMPLE_PAGES = [1, 2, 3]


def main():
    inventory = json.loads((ROOT / "docs/evidence/dataset-inventory.json").read_text())["files"]
    selected = [
        next(x for x in inventory if x["filename"] == "1607.06450v1.pdf"),
        next(x for x in inventory if "_img" in x["filename"]),
        next(x for x in inventory if x["pages"] == 541),
    ]
    values = dotenv_values(ROOT / ".env")
    report = []
    with httpx.Client(trust_env=False, base_url=API, headers={"Origin": "http://127.0.0.1:15173"}, timeout=120) as c:
        c.post(
            "/auth/login", json={"username": "researcher", "password": values["INITIAL_USER_PASSWORD"]}
        ).raise_for_status()
        c.headers["X-CSRF-Token"] = c.cookies["ss_csrf"]
        project = c.get("/projects").json()[0]["id"]
        for item in selected:
            start = time.monotonic()
            path = Path(item["source_path"])
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            with path.open("rb") as f:
                r = c.post(
                    f"/projects/{project}/uploads",
                    files={"file": (path.name, f, "application/pdf")},
                    headers={"Idempotency-Key": uuid4().hex},
                )
                r.raise_for_status()
                job = r.json()
            if "run_id" not in job:
                p = c.post(f"/paper-versions/{job['version_id']}/parses")
                p.raise_for_status()
                job.update(p.json())
            deadline = time.monotonic() + TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                state = c.get(f"/runs/{job['run_id']}").json()
                if state["status"] in ["succeeded", "partially_succeeded", "failed"]:
                    break
                time.sleep(2)
            samples = []
            for n in SAMPLE_PAGES + ([541] if item["pages"] == 541 else []):
                page = c.get(f"/parses/{job['parse_id']}/pages/{n}")
                page.raise_for_status()
                data = page.json()
                samples.append(
                    {
                        "page": n,
                        "chars": data["page"]["text_layer_chars"],
                        "needs_ocr": data["needs_ocr"],
                        "blocks": len(data["blocks"]),
                    }
                )
            assert before == hashlib.sha256(path.read_bytes()).hexdigest()
            report.append(
                {
                    "file": path.name,
                    "expected_pages": item["pages"],
                    "job": job,
                    "state": state,
                    "seconds": round(time.monotonic() - start, 2),
                    "samples": samples,
                    "source_unchanged": True,
                }
            )
            (ROOT / "var/reports/m1-real-pdfs.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(path.name, state["status"], flush=True)
        c.post("/auth/logout").raise_for_status()


if __name__ == "__main__":
    main()
