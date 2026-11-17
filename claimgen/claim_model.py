"""
What a claim is (Pydantic models) and the rules that check it.

    errors, warnings = check(claim)

Each problem is a dict: {"box": "1a", "field": "insurance.member_id", "msg": "..."}
"""
import csv
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent

INSURANCE_TYPES = {
    "medicare": "Medicare", "medicaid": "Medicaid", "tricare": "TRICARE", "champva": "CHAMPVA",
    "group": "Group Health Plan", "feca": "FECA / Black Lung", "other": "Other",
}
SEXES = {"M": "Male", "F": "Female"}
RELATIONSHIPS = {"self": "Self", "spouse": "Spouse", "child": "Child", "other": "Other"}
MAX_LINES_PER_PAGE = 6
DX_LETTERS = "ABCDEFGHIJKL"


def _load_csv(name):
    with open(ROOT / "data" / name, newline="", encoding="utf-8") as f:
        return {r["code"]: r["description"] for r in csv.DictReader(f)}


ICD10 = _load_csv("icd10_common.csv")
POS = _load_csv("pos_codes.csv")


# --------------------------------------------------------------------------
# The claim
# --------------------------------------------------------------------------
class Address(BaseModel):
    line1: str = ""
    line2: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""

    def one_line(self):
        street = " ".join(x for x in (self.line1, self.line2) if x)
        return ", ".join(x for x in (street, self.city, f"{self.state} {self.zip}".strip()) if x)


class Insurance(BaseModel):
    type: Optional[str] = None            # box 1
    member_id: str = ""                   # box 1a
    plan_name: str = ""                   # box 11c
    group_number: str = ""                # box 11
    payer_address: str = ""               # top-right carrier block


class Patient(BaseModel):
    last_name: str = ""                   # box 2
    first_name: str = ""
    middle_initial: str = ""
    dob: Optional[date] = None            # box 3
    sex: Optional[str] = None
    address: Address = Field(default_factory=Address)   # box 5
    phone: str = ""
    account_number: str = ""              # box 26
    relationship_to_insured: Optional[str] = "self"     # box 6


class Insured(BaseModel):                 # boxes 4, 7, 11a
    same_as_patient: bool = True
    last_name: str = ""
    first_name: str = ""
    middle_initial: str = ""
    dob: Optional[date] = None
    sex: Optional[str] = None
    address: Address = Field(default_factory=Address)


class Diagnosis(BaseModel):               # box 21
    letter: str
    code: str


class Line(BaseModel):                    # box 24
    date_from: Optional[date] = None      # 24A
    date_to: Optional[date] = None
    pos: str = ""                         # 24B
    emg: bool = False                     # 24C
    code: str = ""                        # 24D
    modifiers: list[str] = Field(default_factory=list)
    dx_pointer: str = ""                  # 24E  e.g. "A" or "AB"
    charge: float = 0.0                   # 24F
    units: int = 1                        # 24G
    rendering_npi: str = ""               # 24J


class Provider(BaseModel):                # boxes 25, 32, 33
    name: str = ""
    npi: str = ""
    tax_id: str = ""
    phone: str = ""
    address: Address = Field(default_factory=Address)


class Claim(BaseModel):
    claim_id: str = ""
    created_at: Optional[datetime] = None
    status: str = "draft"                 # draft | ready
    form: str = "CMS-1500 (02/12)"
    source: dict = Field(default_factory=lambda: {"type": "manual"})
    insurance: Insurance = Field(default_factory=Insurance)
    patient: Patient = Field(default_factory=Patient)
    insured: Insured = Field(default_factory=Insured)
    diagnoses: list[Diagnosis] = Field(default_factory=list)
    lines: list[Line] = Field(default_factory=list)
    amount_paid: float = 0.0              # box 29
    billing_provider: Provider = Field(default_factory=Provider)
    service_facility_same: bool = True
    service_facility: Provider = Field(default_factory=Provider)   # box 32
    complaint_category: str = ""          # not a CMS-1500 box: kind of problem (helps pick diagnoses)
    accept_assignment: bool = True        # box 27
    signature_on_file: bool = True        # boxes 12, 13, 31
    signature_date: Optional[date] = None
    field_sources: dict = Field(default_factory=dict)   # path -> {"from": bill|guess|manual, "confidence": x}
    validation: dict = Field(default_factory=lambda: {"errors": [], "warnings": []})

    @property
    def total_charge(self):
        return round(sum(cents(l.charge) for l in self.lines) / 100, 2)

    def to_json_dict(self):
        d = self.model_dump(mode="json")
        d["totals"] = {"total_charge": self.total_charge, "amount_paid": round(self.amount_paid, 2)}
        return d


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------
def cents(x):
    return int(round((x or 0) * 100))


def npi_ok(npi):
    """10 digits + Luhn check digit computed with the 80840 prefix."""
    if not re.fullmatch(r"\d{10}", npi or ""):
        return False
    digits = [int(c) for c in "80840" + npi[:9]]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10 == int(npi[9])


ICD_RE = re.compile(r"^[A-TV-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$")
CPT_RE = re.compile(r"^\d{4}[0-9FTU]$")
HCPCS_RE = re.compile(r"^[A-V]\d{4}$")
EIN_RE = re.compile(r"^\d{2}-\d{7}$")
STATE_RE = re.compile(r"^[A-Z]{2}$")
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")


def check(c: Claim, today: Optional[date] = None):
    today = today or date.today()
    errors, warnings = [], []

    def err(box, field, msg):
        errors.append({"box": box, "field": field, "msg": msg})

    def warn(box, field, msg):
        warnings.append({"box": box, "field": field, "msg": msg})

    def need(box, field, value, label):
        if value in (None, "", []):
            err(box, field, f"{label} is required")
            return False
        return True

    def check_address(box, field, a: Address, who):
        need(box, field + ".line1", a.line1, f"{who} street")
        need(box, field + ".city", a.city, f"{who} city")
        if need(box, field + ".state", a.state, f"{who} state") and not STATE_RE.match(a.state):
            err(box, field + ".state", f"{who} state must be 2 capital letters (e.g. TX)")
        if need(box, field + ".zip", a.zip, f"{who} ZIP") and not ZIP_RE.match(a.zip):
            err(box, field + ".zip", f"{who} ZIP must be 5 digits (or 5+4)")

    # --- insurance + patient
    need("1", "insurance.type", c.insurance.type, "Insurance type")
    need("1a", "insurance.member_id", c.insurance.member_id.strip(), "Insured's ID number")
    if not c.insurance.plan_name:
        warn("11c", "insurance.plan_name", "Insurance plan name is empty (the bill showed no insurance?)")

    p = c.patient
    need("2", "patient.last_name", p.last_name.strip(), "Patient last name")
    need("2", "patient.first_name", p.first_name.strip(), "Patient first name")
    if need("3", "patient.dob", p.dob, "Patient birth date") and p.dob > today:
        err("3", "patient.dob", "Patient birth date is in the future")
    need("3", "patient.sex", p.sex, "Patient sex")
    check_address("5", "patient.address", p.address, "Patient")
    need("6", "patient.relationship_to_insured", p.relationship_to_insured, "Relationship to insured")
    need("26", "patient.account_number", p.account_number.strip(), "Patient account number")

    if not c.insured.same_as_patient:
        need("4", "insured.last_name", c.insured.last_name.strip(), "Insured last name")
        need("4", "insured.first_name", c.insured.first_name.strip(), "Insured first name")
        check_address("7", "insured.address", c.insured.address, "Insured")
    elif p.relationship_to_insured not in (None, "self"):
        err("6", "patient.relationship_to_insured",
            "Relationship is not 'Self' but insured is marked 'same as patient'")

    # --- diagnoses
    if not c.diagnoses:
        err("21", "diagnoses", "At least one diagnosis code (ICD-10) is required")
    if len(c.diagnoses) > 12:
        err("21", "diagnoses", "No more than 12 diagnosis codes (A-L)")
    letters = set()
    for i, dx in enumerate(c.diagnoses):
        code = dx.code.strip().upper()
        letters.add(dx.letter)
        if not ICD_RE.match(code):
            err("21", f"diagnoses[{i}]", f"{dx.letter}: '{dx.code}' is not a valid ICD-10 format (e.g. J02.9)")
        elif code not in ICD10:
            warn("21", f"diagnoses[{i}]", f"{dx.letter}: {code} is not in our common-code list — double-check it")

    # --- service lines
    if not c.lines:
        err("24", "lines", "At least one service line is required")
    seen = set()
    for i, ln in enumerate(c.lines):
        n = f"Line {i + 1}"
        f = f"lines[{i}]"
        if need("24A", f + ".date_from", ln.date_from, f"{n}: service date"):
            if ln.date_from > today:
                err("24A", f + ".date_from", f"{n}: service date is in the future")
            if ln.date_to and ln.date_to < ln.date_from:
                err("24A", f + ".date_to", f"{n}: 'to' date is before 'from' date")
            if p.dob and ln.date_from < p.dob:
                err("24A", f + ".date_from", f"{n}: service date is before the patient's birth date")
            bill_date = c.source.get("bill_date")
            if bill_date and ln.date_from > date.fromisoformat(bill_date):
                err("24A", f + ".date_from", f"{n}: service date is after the bill date")
        if need("24B", f + ".pos", ln.pos, f"{n}: place of service") and ln.pos not in POS:
            err("24B", f + ".pos", f"{n}: unknown place of service code '{ln.pos}'")
        code = ln.code.strip().upper()
        if need("24D", f + ".code", code, f"{n}: procedure code"):
            if not (CPT_RE.match(code) or HCPCS_RE.match(code)):
                err("24D", f + ".code", f"{n}: '{code}' is not a CPT (#####) or HCPCS (A####) code")
            elif code.startswith("D"):
                warn("24D", f + ".code", f"{n}: {code} is a dental code — dental claims normally use the ADA form")
        if len(ln.modifiers) > 4:
            err("24D", f + ".modifiers", f"{n}: no more than 4 modifiers")
        for m in ln.modifiers:
            if not re.fullmatch(r"[0-9A-Z]{2}", m):
                err("24D", f + ".modifiers", f"{n}: modifier '{m}' must be 2 letters/digits")
        ptr = ln.dx_pointer.strip().upper()
        if need("24E", f + ".dx_pointer", ptr, f"{n}: diagnosis pointer"):
            if len(ptr) > 4 or not all(ch in DX_LETTERS for ch in ptr):
                err("24E", f + ".dx_pointer", f"{n}: pointer must be 1-4 letters A-L (e.g. A or AB)")
            elif c.diagnoses and any(ch not in letters for ch in ptr):
                err("24E", f + ".dx_pointer", f"{n}: pointer '{ptr}' points to an empty diagnosis")
        if ln.charge is None or cents(ln.charge) <= 0:
            err("24F", f + ".charge", f"{n}: charge must be more than 0")
        if not ln.units or ln.units < 1:
            err("24G", f + ".units", f"{n}: units must be 1 or more")
        if ln.rendering_npi and not npi_ok(ln.rendering_npi):
            err("24J", f + ".rendering_npi", f"{n}: rendering NPI is not valid")
        key = (code, ln.date_from)
        if key in seen:
            warn("24", f, f"{n}: same code and date as an earlier line — possible duplicate")
        seen.add(key)
    if len(c.lines) > MAX_LINES_PER_PAGE:
        pages = -(-len(c.lines) // MAX_LINES_PER_PAGE)
        warn("24", "lines", f"{len(c.lines)} lines: the claim will print on {pages} pages (6 lines per page)")

    # --- money
    if cents(c.amount_paid) < 0:
        err("29", "amount_paid", "Amount paid cannot be negative")
    if cents(c.amount_paid) > sum(cents(l.charge) for l in c.lines):
        err("29", "amount_paid", "Amount paid is more than the total charge")

    # --- provider
    bp = c.billing_provider
    need("33", "billing_provider.name", bp.name.strip(), "Billing provider name")
    check_address("33", "billing_provider.address", bp.address, "Billing provider")
    if need("33a", "billing_provider.npi", bp.npi, "Billing provider NPI") and not npi_ok(bp.npi):
        err("33a", "billing_provider.npi", "Billing provider NPI is not valid (10 digits + check digit)")
    if need("25", "billing_provider.tax_id", bp.tax_id, "Federal tax ID (EIN)") and not EIN_RE.match(bp.tax_id):
        err("25", "billing_provider.tax_id", "Tax ID must look like 12-3456789")
    if not c.service_facility_same:
        sf = c.service_facility
        need("32", "service_facility.name", sf.name.strip(), "Service facility name")
        check_address("32", "service_facility.address", sf.address, "Service facility")
        if sf.npi and not npi_ok(sf.npi):
            err("32a", "service_facility.npi", "Service facility NPI is not valid")

    if not c.signature_on_file:
        warn("12", "signature_on_file", "Signature not on file — the claim will need real signatures")

    # --- guessed values nobody has looked at
    for path, src in sorted(c.field_sources.items()):
        if src.get("from") == "guess" or src.get("confidence", 1) < 0.9:
            warn(GUESS_BOX.get(path.split(".")[-1], ""), path,
                 f"{FRIENDLY.get(path.split('.')[-1], path)} was guessed — please check ({path})")

    return errors, warnings


GUESS_BOX = {"pos": "24B", "dx_pointer": "24E", "type": "1", "sex": "3", "diagnoses": "21"}
FRIENDLY = {"pos": "Place of service", "dx_pointer": "Diagnosis pointer", "type": "Insurance type",
            "sex": "Patient sex", "diagnoses": "Diagnosis codes", "complaint_category": "Complaint category"}
