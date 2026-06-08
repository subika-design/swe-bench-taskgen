from pathlib import Path
import json

out_path = Path("/Users/subika/Turing/lazarus-repo-eval-kit/output/rubric_accepted_pr_urls.txt")
data = json.loads(Path("output/repo.json").read_text(encoding="utf-8"))

GOAL_STATUSES = {"accepted", "partially_accepted"}


def _status(row: dict) -> str:
    value = row.get("rubric_accepted")
    if value is True:
        return "accepted"
    if value is False:
        return "rejected"
    return value or "rejected"


lines = [
    row["url"]
    for row in (data.get("pr_rubrics") or [])
    if _status(row) in GOAL_STATUSES and row.get("url")
]
out_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
print(f"wrote {len(lines)} urls to {out_path}")
