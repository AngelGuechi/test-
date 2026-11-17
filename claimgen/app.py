"""
Easy Claim AI — turn a bill into a CMS-1500 claim, or type one in by hand.

    streamlit run app.py
"""
import io
import json
import math
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw

from batch import run_batch
from claim_model import (ICD10, INSURANCE_TYPES, POS, RELATIONSHIPS, SEXES, Claim, Line, check, cents)
from mapping import CATEGORIES, guess_category, guess_sex, suggested_dx, HINTS
from extractors import extract_with_model, find_label_for_image, from_json
from mapping import bill_to_claim, set_diagnoses
from store import save_claim

ROOT = Path(__file__).resolve().parent
APP_NAME = "Easy Claim AI"
st.set_page_config(page_title=APP_NAME, layout="wide", initial_sidebar_state="collapsed")

# status dots: red = required & empty, orange = filled in for you, green = from the bill
ICON = {"missing": ":red[●]", "guess": ":orange[●]", "bill": ":green[●]", "manual": ""}
RED, YELLOW, GREEN = ICON["missing"], ICON["guess"], ICON["bill"]
BOX_COLORS = {"provider": (31, 95, 191), "patient": (180, 35, 24), "bill_date": (6, 118, 71),
              "due_date": (6, 118, 71), "line_items": (181, 71, 8), "totals": (92, 60, 160)}

st.markdown("""
<style>
header[data-testid="stHeader"], [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="stExpandSidebarButton"], #MainMenu, footer {display: none !important;}
.block-container {padding-top: 1.1rem; padding-bottom: 3rem; max-width: 1280px;}
.ec-brand {display: flex; align-items: center; gap: 12px; height: 42px;}
.ec-logo {width: 36px; height: 36px; border-radius: 8px; background: #1F5FBF; color: #fff;
          font-weight: 700; font-size: 15px; display: flex; align-items: center; justify-content: center;
          letter-spacing: .5px;}
.ec-word {font-size: 1.7rem; font-weight: 700; color: #0F2747; letter-spacing: -.02em; line-height: 1;}
.ec-word span {color: #1F5FBF;}
.ec-rule {border: none; border-top: 1px solid #E3EAF4; margin: .5rem 0 1.4rem;}
.ec-title {font-size: 1.55rem; font-weight: 650; color: #0F2747; margin: 0; line-height: 1.25;}
.ec-sub {color: #5B6B82; font-size: .95rem; margin: 4px 0 0;}
.ec-chip {display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: .8rem;
          font-weight: 600; margin: 0 6px 0 0; white-space: nowrap;}
.ec-chip.red {background: #FDECEC; color: #B42318;}
.ec-chip.amber {background: #FEF4E6; color: #B54708;}
.ec-chip.green {background: #E8F5EE; color: #067647;}
.ec-legend {color: #5B6B82; font-size: .82rem;}
</style>
""", unsafe_allow_html=True)


def page_header(title, sub="", chips_html=""):
    st.markdown(f'<div class="ec-title">{title}</div>'
                + (f'<div class="ec-sub">{sub}</div>' if sub else "")
                + (f'<div style="margin-top:10px">{chips_html}</div>' if chips_html else ""),
                unsafe_allow_html=True)


def chip(text, color):
    return f'<span class="ec-chip {color}">{text}</span>'


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
def default_billgen_folder():
    for cand in (ROOT.parent / "billgen" / "out", ROOT.parent / "billgen" / "billgen" / "out",
                 ROOT.parent.parent / "billgen" / "billgen" / "out", ROOT.parent.parent / "billgen" / "out",
                 Path.home() / "Desktop" / "billgen" / "billgen" / "out"):
        if (cand / "labels").exists():
            return str(cand)
    return ""


ss = st.session_state
ss.setdefault("claim", None)
ss.setdefault("v", 0)                 # bumps on every new claim so widgets start fresh
ss.setdefault("bill", None)
ss.setdefault("image", None)
ss.setdefault("saved", None)
ss.setdefault("billgen_folder", default_billgen_folder())
ss.setdefault("claims_folder", str(ROOT / "claims"))
ss.setdefault("auto_pick_dx", False)


def start(claim: Claim, bill=None, image=None):
    ss.claim, ss.bill, ss.image, ss.saved = claim, bill, image, None
    ss.v += 1


def reset():
    ss.claim, ss.bill, ss.image, ss.saved = None, None, None, None
    ss.v += 1


def K(name):                       # widget key for the current claim
    return f"v{ss.v}:{name}"


# --------------------------------------------------------------------------
# Field helpers: read/write by path, with a colored status dot
# --------------------------------------------------------------------------
def get_path(obj, path):
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def set_path(obj, path, value):
    *parents, last = path.split(".")
    for part in parents:
        obj = getattr(obj, part)
    setattr(obj, last, value)


def status(c: Claim, path, value, required):
    empty = value in (None, "", [])
    if empty and required:
        return "missing"
    src, p = None, path
    while p and src is None:                      # look at the field, then its parents
        src = c.field_sources.get(p)
        p = p.rpartition(".")[0]
    if not src or empty or src.get("from") == "manual":
        return "manual"
    if src.get("from") == "guess" or src.get("confidence", 1) < 0.9:
        return "guess"
    return "bill"


def touch(c: Claim, path):
    c.field_sources[path] = {"from": "manual"}


def field(c: Claim, label, path, kind="text", required=False, options=None, fmt=None, upper=False, help=None):
    cur = get_path(c, path)
    key = K(path)
    now = ss.get(key, cur)                       # what's in the box right now (avoids a one-step lag)
    if isinstance(now, str):
        now = now.strip()
    lab = f"{ICON[status(c, path, now, required)]} {label}".strip()
    fresh = key not in ss                       # only pass a starting value the first time
    if kind == "text":
        val = st.text_input(lab, key=key, help=help, **({"value": cur or ""} if fresh else {}))
        val = val.strip().upper() if upper else val.strip()
    elif kind == "date":
        val = st.date_input(lab, key=key, min_value=date(1900, 1, 1), max_value=date(2100, 12, 31),
                            format="MM/DD/YYYY", help=help, **({"value": cur} if fresh else {}))
    elif kind == "select":
        opts = list(options)
        val = st.selectbox(lab, opts, key=key, format_func=fmt or str, placeholder="Choose…", help=help,
                           **({"index": opts.index(cur) if cur in opts else None} if fresh else {}))
    elif kind == "check":
        val = st.checkbox(lab, key=key, help=help, **({"value": bool(cur)} if fresh else {}))
    elif kind == "money":
        val = round(st.number_input(lab, min_value=0.0, step=1.0, format="%.2f", key=key, help=help,
                                    **({"value": float(cur or 0)} if fresh else {})), 2)
    else:
        raise ValueError(kind)
    if val != cur:
        set_path(c, path, val)
        touch(c, path)
    return val


def address(c: Claim, base, who):
    field(c, f"{who} street", base + ".line1", required=True)
    field(c, "Apt / Suite", base + ".line2")
    a, b, z = st.columns([3, 1, 2])
    with a:
        field(c, "City", base + ".city", required=True)
    with b:
        field(c, "State", base + ".state", required=True, upper=True, help="2 letters, e.g. TX")
    with z:
        field(c, "ZIP", base + ".zip", required=True)


# --------------------------------------------------------------------------
# Start screen
# --------------------------------------------------------------------------
def load_bill(bill, image=None, how="bill"):
    claim = bill_to_claim(bill, {"type": how}, auto_pick_dx=ss.auto_pick_dx)
    start(claim, bill, image)


def on_image_upload():
    up = ss.get("up_image")
    if not up:
        return
    data = up.getvalue()
    bill = extract_with_model(data)
    how = "model"
    if bill is None:
        bill = find_label_for_image(up.name, [ss.billgen_folder])
        how = "billgen label"
    if bill is None:
        start(Claim(source={"type": "image", "file": up.name}), None, data)
        ss.flash = ("info", "No reader is set up for this image yet, so the form is empty. "
                            "Type the fields while looking at the bill on the left.")
    else:
        load_bill(bill, data, how)
        n = len(bill["line_items"])
        ss.flash = ("success", f"Read {n} service line{'s' if n != 1 else ''} from {up.name} ({how}).")


def on_json_upload():
    up = ss.get("up_json")
    if not up:
        return
    try:
        bill = from_json(up.getvalue())
    except Exception as e:  # noqa: BLE001
        ss.flash = ("error", f"Could not read that file: {e}")
        return
    img = None
    if bill.get("image", {}).get("file") and ss.billgen_folder:
        p = Path(ss.billgen_folder) / "images" / bill["image"]["file"]
        img = p.read_bytes() if p.exists() else None
    load_bill(bill, img, "bill json")
    ss.flash = ("success", f"Loaded {up.name}.")


def start_screen():
    page_header("New claim", "Start from a bill image, a bill file, or an empty form.")
    st.write("")
    a, b, c = st.columns(3)
    with a, st.container(border=True):
        st.markdown("##### Bill image")
        st.caption("PNG or JPG of a billing statement.")
        st.file_uploader("Bill image", type=["png", "jpg", "jpeg"], key="up_image", on_change=on_image_upload,
                         label_visibility="collapsed")
    with b, st.container(border=True):
        st.markdown("##### Bill data file")
        st.caption("JSON from the bill generator or the extraction model.")
        st.file_uploader("Bill JSON", type=["json"], key="up_json", on_change=on_json_upload,
                         label_visibility="collapsed")
    with c, st.container(border=True):
        st.markdown("##### Manual entry")
        st.caption("Type every field yourself.")
        if st.button("Start empty claim", type="primary", width="stretch"):
            start(Claim(source={"type": "manual"}))
            st.rerun()

    folder = Path(ss.billgen_folder) if ss.billgen_folder else None
    if folder and (folder / "labels").exists():
        with st.expander("Open a bill from the bill generator folder"):
            files = sorted((folder / "labels").glob("*.json"))[:2000]
            pick = st.selectbox("Bill", [f.stem for f in files], index=None, placeholder="Choose a bill…")
            if pick and st.button("Load this bill"):
                bill = from_json(folder / "labels" / f"{pick}.json")
                ip = folder / "images" / bill.get("image", {}).get("file", "")
                load_bill(bill, ip.read_bytes() if ip.is_file() else None, "billgen label")
                st.rerun()


# --------------------------------------------------------------------------
# Edit screen
# --------------------------------------------------------------------------
def bill_image(show_boxes):
    img = Image.open(io.BytesIO(ss.image)).convert("RGB")
    if show_boxes and ss.bill and ss.bill.get("fields"):
        d = ImageDraw.Draw(img)
        for f in ss.bill["fields"]:
            col = BOX_COLORS.get(f["field"].split(".")[0].split("[")[0], (23, 190, 207))
            pts = [tuple(p) for p in f.get("quad") or [(f["box"][0], f["box"][1]), (f["box"][2], f["box"][1]),
                                                     (f["box"][2], f["box"][3]), (f["box"][0], f["box"][3])]]
            d.polygon(pts, outline=col, width=3)
    return img


def tab_patient(c: Claim):
    st.markdown("##### Insurance")
    a, b = st.columns(2)
    with a:
        field(c, "Insurance type (box 1)", "insurance.type", "select", True, INSURANCE_TYPES,
              fmt=lambda k: INSURANCE_TYPES[k])
        field(c, "Insured's ID / member number (1a)", "insurance.member_id", required=True)
    with b:
        field(c, "Insurance plan name (11c)", "insurance.plan_name")
        field(c, "Group number (11)", "insurance.group_number")
    with st.expander("Insurance company mailing address (optional)"):
        cur = c.insurance.payer_address
        val = st.text_area("Address", value=cur, key=K("insurance.payer_address"), height=80)
        if val != cur:
            c.insurance.payer_address = val
            touch(c, "insurance.payer_address")

    st.markdown("##### Patient")
    a, b, m = st.columns([3, 3, 1])
    with a:
        field(c, "Last name (box 2)", "patient.last_name", required=True)
    with b:
        field(c, "First name", "patient.first_name", required=True)
    sk = K("patient.sex")
    if not c.patient.sex and not ss.get(sk) and c.field_sources.get("patient.sex", {}).get("from") != "manual":
        g = guess_sex(c.patient.first_name)
        if g:                                           # filled in for you, shown orange to check
            c.patient.sex = g
            c.field_sources["patient.sex"] = {"from": "guess", "confidence": 0.7}
            ss[sk] = g
    with m:
        field(c, "M.I.", "patient.middle_initial", upper=True)
    a, b, r = st.columns(3)
    with a:
        field(c, "Birth date (box 3)", "patient.dob", "date", True)
    with b:
        field(c, "Sex (box 3)", "patient.sex", "select", True, SEXES, fmt=lambda k: SEXES[k])
    with r:
        field(c, "Relationship to insured (6)", "patient.relationship_to_insured", "select", True, RELATIONSHIPS,
              fmt=lambda k: RELATIONSHIPS[k])
    address(c, "patient.address", "Patient")
    a, b = st.columns(2)
    with a:
        field(c, "Patient account number (26)", "patient.account_number", required=True,
              help="The ID that links the patient to the provider's system")
    with b:
        field(c, "Patient phone", "patient.phone")

    st.markdown("##### Insured person (boxes 4, 7, 11a)")
    same = field(c, "Insured is the same person as the patient", "insured.same_as_patient", "check")
    if not same:
        a, b = st.columns(2)
        with a:
            field(c, "Insured last name", "insured.last_name", required=True)
            field(c, "Insured birth date", "insured.dob", "date")
        with b:
            field(c, "Insured first name", "insured.first_name", required=True)
            field(c, "Insured sex", "insured.sex", "select", options=SEXES, fmt=lambda k: SEXES[k])
        address(c, "insured.address", "Insured")


def lines_to_df(lines):
    return pd.DataFrame([{
        "from": l.date_from, "to": l.date_to or l.date_from, "pos": l.pos or None, "emg": l.emg,
        "code": l.code, "modifiers": " ".join(l.modifiers), "dx_pointer": l.dx_pointer,
        "charge": float(l.charge), "units": int(l.units), "rendering_npi": l.rendering_npi,
    } for l in lines], columns=["from", "to", "pos", "emg", "code", "modifiers", "dx_pointer", "charge", "units",
                                "rendering_npi"])


def _clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NaT:
        return None
    if isinstance(v, pd.Timestamp):
        return v.date()
    return v


def tab_services(c: Claim):
    st.markdown("##### Service lines (box 24)")
    guessed = [p for p, s in c.field_sources.items() if s.get("from") == "guess"]
    if guessed:
        st.caption(f"{YELLOW} **Place of service** and **diagnosis pointer** were filled in for you — please check them.")
    init_key = K("lines_init")
    if init_key not in ss:
        ss[init_key] = lines_to_df(c.lines)
    init = ss[init_key]
    df = st.data_editor(
        init, key=K("lines"), num_rows="dynamic", width="stretch", hide_index=False,
        column_config={
            "from": st.column_config.DateColumn("From (24A)", format="MM/DD/YYYY", required=True),
            "to": st.column_config.DateColumn("To", format="MM/DD/YYYY"),
            "pos": st.column_config.SelectboxColumn("POS (24B)", options=list(POS), required=True,
                                                    help="Place of service: " + ", ".join(
                                                        f"{k} {v}" for k, v in POS.items())),
            "emg": st.column_config.CheckboxColumn("EMG", default=False),
            "code": st.column_config.TextColumn("Code (24D)", required=True, max_chars=5),
            "modifiers": st.column_config.TextColumn("Modifiers", help="Up to 4, separated by spaces, e.g. 25 59"),
            "dx_pointer": st.column_config.TextColumn("Dx ptr (24E)", default="A", max_chars=4,
                                                      help="Letters of the diagnoses for this line, e.g. A or AB"),
            "charge": st.column_config.NumberColumn("Charge $ (24F)", min_value=0.0, format="%.2f", required=True),
            "units": st.column_config.NumberColumn("Units (24G)", min_value=1, step=1, default=1, required=True),
            "rendering_npi": st.column_config.TextColumn("Rendering NPI (24J)", max_chars=10),
        })
    new_lines = []
    for i, row in df.reset_index(drop=True).iterrows():
        r = {k: _clean(v) for k, v in row.items()}
        new_lines.append(Line(
            date_from=r["from"], date_to=r["to"] or r["from"], pos=str(r["pos"] or ""), emg=bool(r["emg"]),
            code=str(r["code"] or "").strip().upper(),
            modifiers=[m for m in str(r["modifiers"] or "").upper().replace(",", " ").split() if m],
            dx_pointer=str(r["dx_pointer"] or "").strip().upper(), charge=float(r["charge"] or 0),
            units=int(r["units"] or 1), rendering_npi=str(r["rendering_npi"] or "").strip()))
        # cells a person changed become 'manual'
        if i < len(init):
            o = {k: _clean(v) for k, v in init.iloc[i].items()}
            for col, attr in [("from", "date_from"), ("pos", "pos"), ("code", "code"), ("dx_pointer", "dx_pointer"),
                              ("charge", "charge"), ("units", "units")]:
                if r[col] != o[col]:
                    touch(c, f"lines[{i}].{attr}")
    for p in [p for p in c.field_sources if p.startswith("lines[")]:
        if int(p[6:p.index("]")]) >= len(new_lines):
            del c.field_sources[p]
    c.lines = new_lines


    # ---- complaint category (filled from the procedure codes; not a CMS-1500 box)
    codes = [l.code for l in c.lines if l.code]
    ck = K("complaint_category")
    src = c.field_sources.get("complaint_category", {})
    if src.get("from") != "manual":                       # keep following the codes until a person picks one
        g = guess_category(codes)
        if g and g != c.complaint_category:
            c.complaint_category = g
            c.field_sources["complaint_category"] = {"from": "guess", "confidence": 0.7}
            ss[ck] = g
    st.markdown("##### Complaint category")
    field(c, "Kind of problem (filled in from the procedure codes)", "complaint_category", "select",
          options=CATEGORIES, help="Used to put the most likely diagnosis codes at the top of the list below")

    # ---- diagnoses (box 21)
    st.markdown("##### Diagnosis codes — ICD-10 (box 21)")
    cur = [d.code for d in c.diagnoses]
    dk = K("diagnoses")
    if not cur and c.field_sources.get("diagnoses", {}).get("from") != "manual":
        implied = []
        for code in codes:                                 # e.g. checkup -> Z00.00, flu shot -> Z23
            imp = HINTS.get(code, {}).get("implied")
            if imp and imp not in implied:
                implied.append(imp)
        if implied and not ss.get(dk):
            set_diagnoses(c, implied)
            c.field_sources["diagnoses"] = {"from": "guess", "confidence": 0.6, "how": "implied by procedure code"}
            cur = implied
            if dk in ss:
                ss[dk] = implied
    sug = suggested_dx(codes)
    if c.complaint_category:
        for h in HINTS.values():
            if h["category"] == c.complaint_category:
                sug += [x for x in h["suggested"] if x not in sug]
    opts = sug + sorted((set(ICD10) | set(cur)) - set(sug))
    lab = (f"{ICON[status(c, 'diagnoses', ss.get(dk, cur), True)]} Diagnoses, in order A, B, C… "
           f"(suggested codes are at the top; type to search, or type a new code)")
    picked = st.multiselect(lab, opts, key=dk, **({"default": cur} if dk not in ss else {}), max_selections=12, accept_new_options=True,
                            format_func=lambda x: (f"{x} · {ICD10.get(x, '')}" + ("   — suggested" if x in sug else ""))
                            if x in ICD10 else x,
                            placeholder="Search by code or words, e.g. J02.9 or sore throat")
    picked = [p.strip().upper() for p in picked]
    if picked != cur:
        set_diagnoses(c, picked)
        touch(c, "diagnoses")
    if c.diagnoses:
        how = c.field_sources.get("diagnoses", {}).get("how")
        st.caption("   ".join(f"**{d.letter}** = {d.code}" for d in c.diagnoses)
                   + (f"   ({how} — please confirm with the medical record)" if how else ""))
    else:
        st.caption("Pick the diagnosis from the medical record. The most likely codes for these services are at the top.")

    a, b, d = st.columns(3)
    total = c.total_charge
    a.metric("Total charge (28)", f"${total:,.2f}")
    with b:
        field(c, "Amount paid (29)", "amount_paid", "money", help="Already paid by insurance / patient")
    d.metric("Balance", f"${(cents(total) - cents(c.amount_paid)) / 100:,.2f}")


def tab_provider(c: Claim):
    st.markdown("##### Billing provider (boxes 25, 33)")
    field(c, "Provider name", "billing_provider.name", required=True)
    a, b, p = st.columns(3)
    with a:
        field(c, "NPI (33a)", "billing_provider.npi", required=True, help="10 digits")
    with b:
        field(c, "Federal tax ID / EIN (25)", "billing_provider.tax_id", required=True, help="12-3456789")
    with p:
        field(c, "Phone", "billing_provider.phone")
    address(c, "billing_provider.address", "Provider")

    st.markdown("##### Service facility (box 32)")
    same = field(c, "Service happened at the billing provider's address", "service_facility_same", "check")
    if not same:
        field(c, "Facility name", "service_facility.name", required=True)
        field(c, "Facility NPI (32a)", "service_facility.npi")
        address(c, "service_facility.address", "Facility")

    st.markdown("##### Other")
    a, b, d = st.columns(3)
    with a:
        field(c, "Accept assignment (27)", "accept_assignment", "check")
    with b:
        field(c, "Signature on file (12, 13, 31)", "signature_on_file", "check")
    with d:
        field(c, "Signature date", "signature_date", "date", help="Empty = today")


def tab_check(c: Claim):
    errors, warnings = check(c)
    if errors:
        st.markdown(f"**{len(errors)} item{'s' if len(errors) != 1 else ''} to fix before the claim can be created**")
        for e in errors:
            st.markdown(f"{RED} **Box {e['box']}** — {e['msg']}")
    else:
        st.markdown(chip("No errors", "green") + " The claim can be created.", unsafe_allow_html=True)
    if warnings:
        with st.expander(f"{len(warnings)} item{'s' if len(warnings) != 1 else ''} to review", expanded=not errors):
            for w in warnings:
                st.markdown(f"{YELLOW} {('**Box ' + w['box'] + '** — ') if w['box'] else ''}{w['msg']}")
        if any(s.get("from") == "guess" for s in c.field_sources.values()):
            if st.button("I checked the values filled in for me"):
                for p, s in c.field_sources.items():
                    if s.get("from") == "guess":
                        c.field_sources[p] = {"from": "manual", "reviewed": True}
                st.rerun()

    folder = ss.claims_folder
    a, b = st.columns(2)
    with a:
        if st.button("Create claim (JSON + PDF)", type="primary", disabled=bool(errors),
                     width="stretch"):
            jp, pp = save_claim(c, folder, pdf=True, final=True)
            ss.saved = (str(jp), str(pp))
    with b:
        if st.button("Save as draft", width="stretch",
                     help="Saves what you have so far, even with errors"):
            jp, _ = save_claim(c, Path(folder) / "drafts", pdf=False, final=False)
            st.toast(f"Draft saved: {jp.name}")

    if ss.saved:
        jp, pp = map(Path, ss.saved)
        st.success(f"Claim **{jp.stem}** created and saved in `{jp.parent}`")
        a, b, d = st.columns(3)
        a.download_button("Download PDF", pp.read_bytes(), pp.name, "application/pdf", width="stretch")
        b.download_button("Download JSON", jp.read_bytes(), jp.name, "application/json", width="stretch")
        if d.button("New claim", width="stretch"):
            reset()
            st.rerun()


def edit_screen():
    c: Claim = ss.claim
    src = c.source
    what = {"manual": "Manual entry", "image": f"Image {src.get('file', '')}"}.get(
        src.get("type"), f"From {src.get('bill_id', '')}")
    who = " ".join(x for x in (c.patient.first_name, c.patient.last_name) if x)
    top_l, top_r = st.columns([5, 1], vertical_alignment="center")
    with top_l:
        summary = st.empty()          # filled in after the form, so it shows the latest values
    with top_r:
        if st.button("Start over", width="stretch"):
            reset()
            st.rerun()
    st.markdown(f'<span class="ec-legend">{"&nbsp;&nbsp;".join(["<span style=\'color:#067647\'>●</span> from the bill", "<span style=\'color:#B54708\'>●</span> filled in for you, please check", "<span style=\'color:#B42318\'>●</span> required"])}</span>',
                unsafe_allow_html=True)

    form_area = st.container()
    if ss.image:
        left, right = st.columns([4, 8])
        with left:
            show = st.toggle("Show where fields were read", value=bool(ss.bill and ss.bill.get("fields")))
            st.image(bill_image(show), width="stretch")
        form_area = right
    with form_area:
        t1, t2, t3, t4 = st.tabs(["1 · Patient & insurance", "2 · Services & diagnosis", "3 · Provider",
                                  "4 · Check & create"])
        with t1:
            tab_patient(c)
        with t2:
            tab_services(c)
        with t3:
            tab_provider(c)
        with t4:
            tab_check(c)

    errors, warnings = check(c)
    n_missing = sum(1 for e in errors if "required" in e["msg"])
    chips = ""
    if n_missing:
        chips += chip(f"{n_missing} required", "red")
    if len(errors) - n_missing:
        chips += chip(f"{len(errors) - n_missing} to fix", "red")
    if warnings:
        chips += chip(f"{len(warnings)} to review", "amber")
    if not errors:
        chips += chip("Ready to create", "green")
    with summary.container():
        page_header(f"Claim — {who}" if who else "New claim", what, chips)


# --------------------------------------------------------------------------
# Batch screen
# --------------------------------------------------------------------------
def batch_screen():
    page_header("Batch", "Turn every bill file in a folder into a draft claim, with a report of what each "
                         "one still needs.")
    st.write("")
    default = str(Path(ss.billgen_folder) / "labels") if ss.billgen_folder else ""
    bills = st.text_input("Folder with bill JSON files", value=default)
    out = st.text_input("Save drafts to", value=str(Path(ss.claims_folder) / "drafts"))
    a, b = st.columns(2)
    limit = a.number_input("How many (0 = all)", min_value=0, value=100, step=100)
    pdf = b.checkbox("Also make a PDF for each draft")
    if st.button("Run", type="primary"):
        if not Path(bills).is_dir():
            st.error("That folder does not exist.")
            return
        bar = st.progress(0.0)
        rows = run_batch(bills, out, pdf=pdf, limit=limit or None,
                         progress=lambda n, t: bar.progress(n / t, text=f"{n}/{t}"))
        df = pd.DataFrame(rows)
        st.success(f"Made {len(df)} draft claims in `{out}`")
        need = df["boxes_to_fill"].fillna("").str.split().explode().value_counts()
        st.write("**Boxes a person still has to fill:**", ", ".join(f"{k} ({v})" for k, v in need.items()))
        st.dataframe(df, width="stretch", hide_index=True)
        st.download_button("Download report.csv", (Path(out) / "report.csv").read_bytes(), "report.csv",
                           "text/csv")


# --------------------------------------------------------------------------
brand, nav, settings = st.columns([6, 3, 1], vertical_alignment="center")
with brand:
    st.markdown('<div class="ec-brand"><div class="ec-logo">EC</div>'
                '<div class="ec-word">Easy Claim <span>AI</span></div></div>', unsafe_allow_html=True)
with nav:
    mode = st.segmented_control("Page", ["Claim", "Batch"], default="Claim", key="nav",
                                label_visibility="collapsed", width="stretch") or "Claim"
with settings:
    with st.popover("Settings", width="stretch"):
        ss.billgen_folder = st.text_input("Bill generator 'out' folder", value=ss.billgen_folder,
                                          help="Where billgen saved images/ and labels/")
        ss.claims_folder = st.text_input("Save claims to", value=ss.claims_folder)
        ss.auto_pick_dx = st.toggle(
            "Test mode: auto-pick a diagnosis", value=ss.auto_pick_dx,
            help="For synthetic test data only. When the procedure code does not show the reason, "
                 "use the most likely suggested diagnosis (still marked to check). "
                 "For real claims, diagnoses must come from the medical record.")
st.markdown('<hr class="ec-rule">', unsafe_allow_html=True)

flash = ss.pop("flash", None)
if flash:
    if flash[0] == "success":
        st.toast(flash[1])
    else:
        getattr(st, flash[0])(flash[1])

if mode == "Batch":
    batch_screen()
elif ss.claim is None:
    start_screen()
else:
    edit_screen()
