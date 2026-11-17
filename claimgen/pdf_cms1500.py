"""
Draws a claim as a PDF in the CMS-1500 (02/12) layout (black ink, one page per 6 lines).
This is a readable copy, not the official red OCR form.

    write_pdf(claim, "claims/CLM-....pdf")
"""
from datetime import date

from reportlab.lib.colors import Color, black
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from claim_model import MAX_LINES_PER_PAGE, Claim, cents

X0, W = 22, 568
LABEL_FONT, VALUE_FONT = "Helvetica", "Courier-Bold"
SHADE = Color(0.93, 0.93, 0.93)


def mdy(d, year4=True):
    if not d:
        return ""
    return d.strftime("%m %d %Y" if year4 else "%m %d %y")


def money(x):
    return f"{x:,.2f}"


class Form:
    def __init__(self, c: canvas.Canvas):
        self.c = c

    def box(self, x, top, w, h, num, label, lines=(), size=9.5, right=False):
        c = self.c
        c.setLineWidth(0.6)
        c.rect(x, top - h, w, h)
        c.setFont(LABEL_FONT, 5.6)
        c.drawString(x + 2.5, top - 7, f"{num}. {label}" if num else label)
        c.setFont(VALUE_FONT, size)
        y = top - 7 - size - 1
        for ln in lines:
            if right:
                c.drawRightString(x + w - 4, y, str(ln))
            else:
                c.drawString(x + 4, y, str(ln)[: int((w - 6) / (size * 0.6))])
            y -= size + 1.5

    def check(self, x, y, label, on, size=6):
        c = self.c
        c.setLineWidth(0.5)
        c.rect(x, y, 7, 7)
        if on:
            c.setFont("Helvetica-Bold", 8)
            c.drawString(x + 1.2, y + 1, "X")
        c.setFont(LABEL_FONT, size)
        c.drawString(x + 9, y + 1, label)


def _page(cv, claim: Claim, lines, page_no, pages, is_last):
    f = Form(cv)
    p, ins, bp = claim.patient, claim.insurance, claim.billing_provider
    insured = claim.insured
    same = insured.same_as_patient

    # header
    cv.setFont("Helvetica-Bold", 14)
    cv.drawString(X0, 760, "HEALTH INSURANCE CLAIM FORM")
    cv.setFont(LABEL_FONT, 6.5)
    cv.drawString(X0, 750, "CMS-1500 (02/12) LAYOUT  ·  GENERATED COPY — NOT THE OFFICIAL RED OCR FORM")
    cv.drawString(X0, 741, f"Claim ID: {claim.claim_id or 'DRAFT'}     Page {page_no} of {pages}")
    payer = [ins.plan_name] + [s for s in ins.payer_address.splitlines() if s.strip()]
    f.box(X0 + 360, 772, 208, 46, "", "INSURANCE CARRIER", payer[:3], size=8.5)

    top = 718
    # row 1 — boxes 1, 1a
    h = 30
    f.box(X0, top, 360, h, "1", "MEDICARE  MEDICAID  TRICARE  CHAMPVA  GROUP HEALTH PLAN  FECA BLK LUNG  OTHER")
    for i, (k, lab) in enumerate([("medicare", "MEDICARE"), ("medicaid", "MEDICAID"), ("tricare", "TRICARE"),
                                  ("champva", "CHAMPVA"), ("group", "GROUP"), ("feca", "FECA"), ("other", "OTHER")]):
        f.check(X0 + 5 + i * 51, top - 23, lab, ins.type == k)
    f.box(X0 + 360, top, 208, h, "1a", "INSURED'S I.D. NUMBER", [ins.member_id])
    top -= h

    # row 2 — 2, 3, 4
    h = 30
    name = f"{p.last_name}, {p.first_name}{(', ' + p.middle_initial) if p.middle_initial else ''}"
    f.box(X0, top, 210, h, "2", "PATIENT'S NAME (Last Name, First Name, Middle Initial)", [name.upper()])
    f.box(X0 + 210, top, 150, h, "3", "PATIENT'S BIRTH DATE (MM DD YYYY)      SEX", [mdy(p.dob)], size=9)
    f.check(X0 + 300, top - 24, "M", p.sex == "M")
    f.check(X0 + 325, top - 24, "F", p.sex == "F")
    iname = name if same else f"{insured.last_name}, {insured.first_name}"
    f.box(X0 + 360, top, 208, h, "4", "INSURED'S NAME (Last Name, First Name, Middle Initial)",
          [("SAME" if same else iname.upper())])
    top -= h

    # row 3 — 5, 6, 7
    h = 44
    a = p.address
    f.box(X0, top, 210, h, "5", "PATIENT'S ADDRESS (No., Street)",
          [f"{a.line1} {a.line2}".strip().upper(), f"{a.city}, {a.state} {a.zip}".upper(), p.phone], size=8.5)
    f.box(X0 + 210, top, 150, h, "6", "PATIENT RELATIONSHIP TO INSURED")
    for i, (k, lab) in enumerate([("self", "Self"), ("spouse", "Spouse"), ("child", "Child"), ("other", "Other")]):
        f.check(X0 + 215 + i * 36, top - 22, lab, p.relationship_to_insured == k)
    ia = a if same else insured.address
    f.box(X0 + 360, top, 208, h, "7", "INSURED'S ADDRESS (No., Street)",
          ["SAME"] if same else [f"{ia.line1} {ia.line2}".strip().upper(), f"{ia.city}, {ia.state} {ia.zip}".upper()],
          size=8.5)
    top -= h

    # row 4 — 9, 10, 11
    h = 44
    f.box(X0, top, 210, h, "9", "OTHER INSURED'S NAME", [])
    f.box(X0 + 210, top, 150, h, "10", "IS PATIENT'S CONDITION RELATED TO:")
    for i, lab in enumerate(["a. EMPLOYMENT", "b. AUTO ACCIDENT", "c. OTHER ACCIDENT"]):
        cv.setFont(LABEL_FONT, 6)
        cv.drawString(X0 + 214, top - 17 - i * 9, lab)
        f.check(X0 + 290, top - 19 - i * 9, "YES", False, size=5.5)
        f.check(X0 + 320, top - 19 - i * 9, "NO", True, size=5.5)
    idob = p.dob if same else insured.dob
    isex = p.sex if same else insured.sex
    f.box(X0 + 360, top, 208, h, "11", "INSURED'S POLICY GROUP OR FECA NUMBER",
          [ins.group_number, f"11a DOB {mdy(idob)}  SEX {isex or ''}"], size=8.5)
    top -= h

    # row 5 — 9d, 10d, 11c
    h = 26
    f.box(X0, top, 210, h, "9d", "INSURANCE PLAN NAME OR PROGRAM NAME", [])
    f.box(X0 + 210, top, 150, h, "10d", "CLAIM CODES (Designated by NUCC)", [])
    f.box(X0 + 360, top, 208, h, "11c", "INSURANCE PLAN NAME OR PROGRAM NAME", [ins.plan_name.upper()], size=8.5)
    top -= h

    # row 6 — 12, 13
    h = 28
    sig = "SIGNATURE ON FILE" if claim.signature_on_file else ""
    sdate = mdy(claim.signature_date or (claim.created_at.date() if claim.created_at else None))
    f.box(X0, top, 360, h, "12", "PATIENT'S OR AUTHORIZED PERSON'S SIGNATURE",
          [f"{sig}      DATE {sdate}" if sig else ""], size=8.5)
    f.box(X0 + 360, top, 208, h, "13", "INSURED'S OR AUTHORIZED PERSON'S SIGNATURE", [sig], size=8.5)
    top -= h

    # rows 7-9 — 14..20 (left blank unless known)
    for left, right in [(("14", "DATE OF CURRENT ILLNESS, INJURY, or PREGNANCY (LMP)"),
                         ("16", "DATES PATIENT UNABLE TO WORK IN CURRENT OCCUPATION")),
                        (("17", "NAME OF REFERRING PROVIDER OR OTHER SOURCE      17b. NPI"),
                         ("18", "HOSPITALIZATION DATES RELATED TO CURRENT SERVICES")),
                        (("19", "ADDITIONAL CLAIM INFORMATION (Designated by NUCC)"),
                         ("20", "OUTSIDE LAB?        $ CHARGES"))]:
        f.box(X0, top, 360, 24, left[0], left[1], [])
        f.box(X0 + 360, top, 208, 24, right[0], right[1], ["NO" if right[0] == "20" else ""], size=8)
        top -= 24

    # row 10 — 21, 22, 23
    h = 52
    f.box(X0, top, 360, h, "21", "DIAGNOSIS OR NATURE OF ILLNESS OR INJURY   Relate A-L to service line below (24E)   ICD Ind. 0")
    cv.setFont(VALUE_FONT, 9)
    for i, letter in enumerate("ABCDEFGHIJKL"):
        col, row = i % 4, i // 4
        code = next((d.code for d in claim.diagnoses if d.letter == letter), "")
        x, y = X0 + 6 + col * 88, top - 19 - row * 12
        cv.setFont(LABEL_FONT, 6)
        cv.drawString(x, y, f"{letter}.")
        cv.setFont(VALUE_FONT, 9)
        cv.drawString(x + 9, y, code)
    f.box(X0 + 360, top, 208, h / 2, "22", "RESUBMISSION CODE        ORIGINAL REF. NO.", [])
    f.box(X0 + 360, top - h / 2, 208, h / 2, "23", "PRIOR AUTHORIZATION NUMBER", [])
    top -= h

    # box 24 — service lines
    cols = [("24A", "DATE(S) OF SERVICE\nFrom          To", 112), ("B", "PLACE\nOF SVC", 26),
            ("C", "EMG", 24), ("D", "CPT/HCPCS   MODIFIER", 116), ("E", "DIAG.\nPOINTER", 40),
            ("F", "$ CHARGES", 64), ("G", "DAYS/\nUNITS", 32), ("H", "EPSDT", 26), ("I", "ID.\nQUAL", 24),
            ("J", "RENDERING\nPROVIDER ID. #", 104)]
    hh = 22
    x = X0
    cv.setLineWidth(0.6)
    for key, lab, w in cols:
        cv.rect(x, top - hh, w, hh)
        cv.setFont(LABEL_FONT, 5.6)
        for j, part in enumerate(f"{key}. {lab}".split("\n")):
            cv.drawString(x + 2, top - 7 - j * 6.5, part)
        x += w
    top -= hh
    rh = 28
    for r in range(MAX_LINES_PER_PAGE):
        ln = lines[r] if r < len(lines) else None
        cv.setFillColor(SHADE)
        cv.rect(X0, top - rh / 2, W, rh / 2, stroke=0, fill=1)
        cv.setFillColor(black)
        x = X0
        for key, _, w in cols:
            cv.rect(x, top - rh, w, rh)
            x += w
        cv.setFont(LABEL_FONT, 7)
        cv.drawString(X0 - 9, top - rh + 4, str(r + 1))
        if ln:
            y = top - rh + 5
            vals = [f"{mdy(ln.date_from, False)}  {mdy(ln.date_to or ln.date_from, False)}", ln.pos,
                    "Y" if ln.emg else "", f"{ln.code:<6} {' '.join(ln.modifiers)}", ln.dx_pointer,
                    money(ln.charge), str(ln.units), "", "NPI", ln.rendering_npi]
            x = X0
            for (key, _, w), v in zip(cols, vals):
                cv.setFont(VALUE_FONT, 9)
                if key in ("F", "G"):
                    cv.drawRightString(x + w - 4, y, v)
                else:
                    cv.drawString(x + 4, y, v)
                x += w
        top -= rh

    # row 12 — 25..30
    h = 32
    page_total = sum(cents(l.charge) for l in lines) / 100
    paid = claim.amount_paid if is_last else 0
    f.box(X0, top, 150, h, "25", "FEDERAL TAX I.D. NUMBER     SSN  EIN", [bp.tax_id])
    f.check(X0 + 120, top - 26, "EIN", True, size=5.5)
    f.box(X0 + 150, top, 130, h, "26", "PATIENT'S ACCOUNT NO.", [p.account_number])
    f.box(X0 + 280, top, 90, h, "27", "ACCEPT ASSIGNMENT?")
    f.check(X0 + 286, top - 24, "YES", claim.accept_assignment, size=5.5)
    f.check(X0 + 322, top - 24, "NO", not claim.accept_assignment, size=5.5)
    f.box(X0 + 370, top, 80, h, "28", "TOTAL CHARGE", [money(page_total)], right=True)
    f.box(X0 + 450, top, 60, h, "29", "AMOUNT PAID", [money(paid)], size=8.5, right=True)
    f.box(X0 + 510, top, 58, h, "30", "Rsvd for NUCC Use", [])
    top -= h

    # row 13 — 31, 32, 33
    h = 70
    f.box(X0, top, 170, h, "31", "SIGNATURE OF PHYSICIAN OR SUPPLIER",
          [sig, f"DATE {sdate}" if sig else ""], size=8)
    sf = bp if claim.service_facility_same else claim.service_facility
    sa = sf.address
    f.box(X0 + 170, top, 190, h, "32", "SERVICE FACILITY LOCATION INFORMATION",
          [sf.name.upper(), f"{sa.line1} {sa.line2}".strip().upper(), f"{sa.city}, {sa.state} {sa.zip}".upper(),
           f"32a NPI {sf.npi}"], size=8)
    ba = bp.address
    f.box(X0 + 360, top, 208, h, "33", f"BILLING PROVIDER INFO & PH #   {bp.phone}",
          [bp.name.upper(), f"{ba.line1} {ba.line2}".strip().upper(), f"{ba.city}, {ba.state} {ba.zip}".upper(),
           f"33a NPI {bp.npi}"], size=8)
    top -= h

    cv.setFont(LABEL_FONT, 6.5)
    src = claim.source.get("bill_id") or claim.source.get("type", "")
    cv.drawString(X0, top - 12, f"Generated {date.today().isoformat()} by Easy Claim AI  ·  source: {src}  ·  "
                                f"status: {claim.status}")
    if pages > 1 and not is_last:
        cv.drawRightString(X0 + W, top - 12, "CONTINUED ON NEXT PAGE")


def write_pdf(claim: Claim, path):
    cv = canvas.Canvas(str(path), pagesize=letter)
    cv.setTitle(f"Claim {claim.claim_id or 'DRAFT'}")
    chunks = [claim.lines[i:i + MAX_LINES_PER_PAGE] for i in range(0, len(claim.lines), MAX_LINES_PER_PAGE)] or [[]]
    for i, chunk in enumerate(chunks, 1):
        _page(cv, claim, chunk, i, len(chunks), i == len(chunks))
        cv.showPage()
    cv.save()
    return path
