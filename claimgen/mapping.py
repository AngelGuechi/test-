"""
Bill fields (billgen label JSON, or the extraction model's output in the same shape)
-> a draft Claim. Boxes the bill can't fill are left empty for a person.
"""
import csv
from collections import Counter
from datetime import date
from pathlib import Path

from claim_model import Address, Claim, Diagnosis, Insurance, Line, Patient, Provider

ROOT = Path(__file__).resolve().parent


def _read(name):
    with open(ROOT / "data" / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


NAME_SEX = {r["name"].lower(): r["sex"] for r in _read("first_names_sex.csv")}
HINTS = {r["code"]: {"category": r["category"], "implied": r["implied_dx"].strip(),
                     "suggested": r["suggested_dx"].split()} for r in _read("code_hints.csv")}
CATEGORIES = sorted({h["category"] for h in HINTS.values()})
GENERIC = {"General visit", "Lab work", "Medication / injection"}


def guess_sex(first_name):
    """Most likely sex from the first name (None if unknown or used by both)."""
    return NAME_SEX.get((first_name or "").strip().lower())


def guess_category(codes):
    """Kind of problem, from the procedure codes (specific kinds beat general ones)."""
    cats = Counter(HINTS[c]["category"] for c in codes if c in HINTS)
    if not cats:
        return ""
    specific = Counter({k: v for k, v in cats.items() if k not in GENERIC})
    return (specific or cats).most_common(1)[0][0]


def suggested_dx(codes):
    """Diagnosis codes that usually go with these procedure codes, most likely first."""
    out = []
    for c in codes:
        for dx in ([HINTS[c]["implied"]] if c in HINTS and HINTS[c]["implied"] else []) + \
                  HINTS.get(c, {}).get("suggested", []):
            if dx and dx not in out:
                out.append(dx)
    return out

TITLES = {"mr", "mrs", "ms", "miss", "dr", "mx"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
DEGREES = {"md", "dds", "dvm", "phd", "do", "rn", "np", "pa"}


def split_name(full):
    """'Dr. Mary Ann Smith Jr.' -> ('Smith Jr', 'Mary', 'A')"""
    parts = [p.strip(",") for p in full.replace(".", "").split()]
    parts = [p for p in parts if p.lower() not in TITLES and p.lower() not in DEGREES]
    suffix = ""
    if parts and parts[-1].lower() in SUFFIXES:
        suffix = " " + parts.pop()
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0] + suffix, "", ""
    last = parts[-1] + suffix
    first = parts[0]
    middle = parts[1][0].upper() if len(parts) > 2 else ""
    return last, first, middle


def guess_pos(provider_type, codes):
    """Place of service guessed from the kind of provider (always marked 'guess')."""
    if provider_type == "hospital":
        if any(c.startswith("9928") for c in codes):
            return "23"   # emergency room
        if any(c[:4] in ("9922", "9923") for c in codes):
            return "21"   # inpatient
        return "22"       # outpatient hospital
    return {"urgent_care": "20", "lab": "81"}.get(provider_type, "11")


def _addr(parts):
    parts = parts or {}
    return Address(line1=parts.get("line1", ""), line2=parts.get("line2", ""), city=parts.get("city", ""),
                   state=parts.get("state", ""), zip=parts.get("zip", ""))


def autofill_diagnoses(claim: Claim, auto_pick=False):
    """Fill box 21 + pointers (24E) from the procedure codes.
    - Codes that imply their reason (checkup -> Z00.00, flu shot -> Z23, cleaning -> Z01.20) are always filled.
    - auto_pick=True (test data only): if nothing is implied, use the most likely suggestion.
    Everything filled here is marked 'guess' (yellow) so a person confirms it."""
    codes = [l.code for l in claim.lines]
    dx = []
    for c in codes:
        imp = HINTS.get(c, {}).get("implied")
        if imp and imp not in dx:
            dx.append(imp)
    how = "implied by procedure code"
    if not dx and auto_pick:
        sug = suggested_dx(codes)
        if sug:
            dx, how = sug[:1], "auto-picked suggestion (test mode)"
    if not dx:
        return claim
    set_diagnoses(claim, dx)
    letter = {d.code: d.letter for d in claim.diagnoses}
    for k, ln in enumerate(claim.lines):
        imp = HINTS.get(ln.code, {}).get("implied")
        ln.dx_pointer = letter.get(imp, "A")
        claim.field_sources[f"lines[{k}].dx_pointer"] = {"from": "guess", "confidence": 0.6}
    claim.field_sources["diagnoses"] = {"from": "guess", "confidence": 0.6, "how": how}
    return claim


def bill_to_claim(bill: dict, source: dict | None = None, auto_pick_dx=False) -> Claim:
    conf = bill.get("confidence", {})          # optional: model gives a 0-1 score per field
    src = {}

    def mark(path, bill_path=None):
        src[path] = {"from": "bill", "confidence": round(float(conf.get(bill_path or path, 1.0)), 2)}

    pv, pt = bill.get("provider", {}), bill.get("patient", {})
    last, first, mi = split_name(pt.get("name", ""))
    for f in ("last_name", "first_name", "middle_initial"):
        mark(f"patient.{f}", "patient.name")

    patient = Patient(last_name=last, first_name=first, middle_initial=mi,
                      dob=date.fromisoformat(pt["dob"]) if pt.get("dob") else None,
                      address=_addr(pt.get("address_parts")), account_number=pt.get("account_number", ""))
    for path in ("patient.dob", "patient.address", "patient.account_number"):
        mark(path)

    insurance = Insurance(plan_name=pt.get("insurance") or "")
    if insurance.plan_name:
        mark("insurance.plan_name", "patient.insurance")

    provider = Provider(name=pv.get("name", ""), npi=pv.get("npi", ""), tax_id=pv.get("tax_id", ""),
                        phone=pv.get("phone", ""), address=_addr(pv.get("address_parts")))
    for f in ("name", "npi", "tax_id", "phone", "address"):
        mark(f"billing_provider.{f}", f"provider.{f}")

    items = bill.get("line_items", [])
    pos = guess_pos(pv.get("type", ""), [i.get("code", "") for i in items])
    lines = []
    for k, it in enumerate(items):
        dos = date.fromisoformat(it["date_of_service"]) if it.get("date_of_service") else None
        lines.append(Line(date_from=dos, date_to=dos, pos=pos, code=it.get("code", ""),
                          dx_pointer="A", charge=float(it.get("charge", 0)), units=int(it.get("units", 1)),
                          rendering_npi=pv.get("npi", "")))
        for f in ("date_from", "code", "charge", "units"):
            bill_f = {"date_from": "date_of_service"}.get(f, f)
            mark(f"lines[{k}].{f}", f"line_items[{k}].{bill_f}")
        src[f"lines[{k}].pos"] = {"from": "guess", "confidence": 0.5}
        src[f"lines[{k}].dx_pointer"] = {"from": "guess", "confidence": 0.5}

    amount_paid = float(bill.get("totals", {}).get("insurance_paid", 0) or 0)
    mark("amount_paid", "totals.insurance_paid")

    sex = guess_sex(first)
    if sex:
        patient.sex = sex
        src["patient.sex"] = {"from": "guess", "confidence": 0.7}

    claim = Claim(
        source={"type": "bill", "bill_id": bill.get("id", ""), "bill_date": bill.get("bill_date"),
                **(source or {})},
        insurance=insurance, patient=patient, billing_provider=provider, lines=lines,
        amount_paid=amount_paid, diagnoses=[], field_sources=src,
        complaint_category=guess_category([l.code for l in lines]),
    )
    if claim.complaint_category:
        claim.field_sources["complaint_category"] = {"from": "guess", "confidence": 0.7}
    return autofill_diagnoses(claim, auto_pick_dx)


def set_diagnoses(claim: Claim, codes: list[str]) -> Claim:
    claim.diagnoses = [Diagnosis(letter="ABCDEFGHIJKL"[i], code=c.strip().upper())
                       for i, c in enumerate(codes[:12])]
    return claim
