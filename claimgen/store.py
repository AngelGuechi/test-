"""Save claims as files: claims/CLM-YYYYMMDD-####.json (+ .pdf)."""
import json
from datetime import date, datetime
from pathlib import Path

from claim_model import Claim, check
from pdf_cms1500 import write_pdf


def next_claim_id(folder, today=None):
    today = today or date.today()
    prefix = f"CLM-{today:%Y%m%d}-"
    folder = Path(folder)
    nums = [int(p.stem[len(prefix):]) for p in folder.glob(prefix + "*.json") if p.stem[len(prefix):].isdigit()]
    return f"{prefix}{max(nums, default=0) + 1:04d}"


def save_claim(claim: Claim, folder, pdf=True, final=True):
    """final=True: gets a real claim ID and status 'ready' (only if no errors)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    errors, warnings = check(claim)
    claim.validation = {"errors": errors, "warnings": warnings}
    if final and not errors:
        claim.status = "ready"
        if not claim.claim_id or claim.claim_id.startswith("DRAFT"):
            claim.claim_id = next_claim_id(folder)
    else:
        claim.status = "draft"
        if not claim.claim_id:
            claim.claim_id = "DRAFT-" + (claim.source.get("bill_id") or datetime.now().strftime("%Y%m%d-%H%M%S"))
    claim.created_at = claim.created_at or datetime.now().astimezone()
    jp = folder / f"{claim.claim_id}.json"
    jp.write_text(json.dumps(claim.to_json_dict(), indent=2), encoding="utf-8")
    pp = None
    if pdf:
        pp = folder / f"{claim.claim_id}.pdf"
        write_pdf(claim, pp)
    return jp, pp


def load_claim(path) -> Claim:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    d.pop("totals", None)
    return Claim.model_validate(d)
