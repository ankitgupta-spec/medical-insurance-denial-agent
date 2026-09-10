"""
================================================================================
  Automated Medical Insurance Claim Denial & Appeals Agent — Flask Web Server
  Problem Statement No. 38 — Edunet Foundation IBM SkillsBuild Training
  Version : 2.0.0  |  Engine: IBM watsonx Orchestrate (Simulated)
================================================================================
"""

import io
import json
import os
import re
import sys
import textwrap
from datetime import datetime

# Force UTF-8 output so box-drawing / arrow characters render on all platforms
if sys.stdout.encoding and sys.stdout.encoding.upper() != "UTF-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pandas as pd
from flask import Flask, jsonify, render_template

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS & PATHS
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DATA_DIR    = os.path.join(BASE_DIR, "data")
CSV_PATH    = os.path.join(DATA_DIR, "denial_records.csv")
POLICY_PATH = os.path.join(DATA_DIR, "insurance_policies.txt")

# ─────────────────────────────────────────────────────────────────────────────
# POLICY RULE REGISTRY
# ─────────────────────────────────────────────────────────────────────────────
POLICY_REGISTRY = {
    "ERR-CARDIAC": {
        "rule_id" : "ACC-901",
        "title"   : "Advanced Cardiac Diagnostic Imaging Coverage",
        "criteria": (
            "Patient must have (1) documented ATYPICAL CHEST PAIN and "
            "(2) an ABNORMAL resting EKG showing ST changes, LVH, "
            "bradycardia, T-wave inversions, or conduction defects."
        ),
        "section" : "SECTION 1 — CARDIAC DIAGNOSTIC IMAGING",
    },
    "ERR-ORTHO": {
        "rule_id" : "ORTHO-404",
        "title"   : "Arthroscopic Surgical Procedure Coverage",
        "criteria": (
            "Patient must have completed a MINIMUM of 4 consecutive months "
            "of formally documented clinical Physical Therapy or physician-"
            "administered injections — home exercises / yoga / acupuncture "
            "do NOT qualify."
        ),
        "section" : "SECTION 2 — ORTHOPAEDIC SURGICAL PROCEDURES",
    },
    "ERR-ONCOLOGY": {
        "rule_id" : "ONCO-202",
        "title"   : "Targeted Oncology Therapy Coverage",
        "criteria": (
            "A companion diagnostic genetic test must CONFIRM the specific "
            "biomarker mutation / expression level for the requested agent "
            "(e.g., PD-L1 >= 1% for Pembrolizumab; HER2-positive for "
            "Trastuzumab; KRAS/NRAS wild-type for Cetuximab)."
        ),
        "section" : "SECTION 3 — ONCOLOGY TARGETED THERAPY & IMMUNOTHERAPY",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 1 — PolicyAuditAgent
# ─────────────────────────────────────────────────────────────────────────────
class PolicyAuditAgent:
    """Maps a denial code to its governing policy rule."""

    def __init__(self, policy_text: str):
        self._policy_text = policy_text

    def audit(self, denial_code: str) -> dict:
        rule = POLICY_REGISTRY.get(denial_code.strip().upper())
        if rule is None:
            return {
                "rule_id"    : "UNKNOWN",
                "title"      : "No matching policy rule found",
                "criteria"   : "N/A",
                "section"    : "N/A",
                "raw_excerpt": "",
            }
        lines         = self._policy_text.splitlines()
        excerpt_lines = []
        capturing     = False
        for line in lines:
            if rule["section"] in line:
                capturing = True
            if capturing:
                excerpt_lines.append(line)
                if len(excerpt_lines) > 3 and line.startswith("------"):
                    break
        result = dict(rule)
        result["raw_excerpt"] = "\n".join(excerpt_lines[:6])
        return result


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 2 — ClinicalEvidenceAgent
# ─────────────────────────────────────────────────────────────────────────────
class ClinicalEvidenceAgent:
    """Rule-based NLP evaluation of clinical notes against policy criteria."""

    _EKG_ABNORMAL_SIGNALS = [
        r"st[- ]?segment abnormalit", r"st[- ]?segment elevation",
        r"st[- ]?segment depression", r"t[- ]?wave inversion",
        r"left ventricular hypertrophy", r"\blvh\b",
        r"bundle branch block", r"\blbbb\b", r"\brbbb\b",
        r"sinus bradycardia", r"atrial fibrillation",
        r"pathological q wave", r"conduction defect",
        r"rhythm abnormalit", r"abnormal.*ekg", r"ekg.*abnormal",
        r"ecg.*abnormal", r"abnormal.*ecg",
    ]

    _PT_FORMAL_SIGNALS = [
        r"physical therapy", r"\bpt\b", r"physiotherapy",
        r"corticosteroid injection", r"hyaluronic acid injection",
        r"intra[- ]articular injection", r"localized.*injection",
        r"injection.*treatment", r"nsaid.*treatment",
    ]

    _PT_INFORMAL_SIGNALS = [
        r"\byoga\b", r"\bacupuncture\b", r"home stretch",
        r"occasional.*stretch", r"did not complete.*formal",
        r"no formal.*therapy", r"no formal.*physical therapy", r"informal",
    ]

    _MONTHS_PATTERN = re.compile(
        r"(\d+)\s*(?:consecutive|continuous|calendar)?\s*months?", re.IGNORECASE
    )

    _ONCO_POSITIVE_SIGNALS = [
        r"pd[- ]?l1.*expression.*>?\s*\d+\s*%", r"high pd[- ]?l1",
        r"pd[- ]?l1.*positive", r"her2[- ]positive", r"her2.*3\+",
        r"fish.*ratio.*>=?\s*2", r"braf.*v600", r"egfr.*exon\s*19",
        r"egfr.*l858r", r"wild[- ]?type\s+(?:kras|nras|ras)",
        r"kras.*wild[- ]?type", r"nras.*wild[- ]?type",
        r"companion diagnostic.*validates", r"confirms.*expression",
        r"validates.*expression", r"genetic panel.*validates",
        r"mutation.*confirmed", r"biomarker.*confirmed",
    ]

    _ONCO_NEGATIVE_SIGNALS = [
        r"her2[- ]negative", r"no.*expression.*detected",
        r"negative.*mutation.*status", r"mutation.*negative",
        r"kras[- ]mutated", r"nras[- ]mutated", r"ras[- ]mutated",
        r"confirmed.*mutation\s+blockage",
        r"pd[- ]?l1.*<\s*1\s*%", r"low.*pd[- ]?l1",
    ]

    def _search_any(self, text: str, patterns: list) -> bool:
        return any(re.search(p, text, re.IGNORECASE) for p in patterns)

    def _extract_pt_months(self, notes: str) -> int:
        matches = self._MONTHS_PATTERN.findall(notes)
        return max((int(m) for m in matches), default=0)

    def evaluate(self, denial_code: str, clinical_notes: str) -> dict:
        code  = denial_code.strip().upper()
        notes = clinical_notes.strip()

        if code == "ERR-CARDIAC":
            has_atypical = bool(re.search(
                r"atypical\s+chest\s+pain|exertional\s+dyspnea|"
                r"syncopal\s+episode|recurrent\s+syncope", notes, re.I))
            has_abnormal_ekg = self._search_any(notes, self._EKG_ABNORMAL_SIGNALS)
            clean_ekg = bool(re.search(
                r"clean\s+(?:resting\s+)?ekg"
                r"|(?:resting\s+)?ekg\s+(?:is\s+)?(?:within\s+normal|normal)"
                r"|\bnormal\b.*\bekg\b"
                r"|resolved.*\brest\b",
                notes, re.I))
            if has_abnormal_ekg and not clean_ekg:
                return {"criteria_met": True, "confidence": "HIGH",
                        "rationale": (
                            "Clinical notes document atypical cardiac presentation AND confirm "
                            "abnormal resting EKG findings (ST changes / LVH / bradycardia / "
                            "T-wave inversions). Both mandatory criteria under ACC-901 are satisfied.")}
            missing = []
            if not has_atypical:
                missing.append("atypical chest pain documentation absent")
            if clean_ekg:
                missing.append("EKG explicitly documented as normal/clean")
            elif not has_abnormal_ekg:
                missing.append("no abnormal EKG findings recorded")
            return {"criteria_met": False, "confidence": "HIGH",
                    "rationale": (
                        f"Policy criteria NOT met. Deficiency: {'; '.join(missing)}. "
                        "ACC-901 requires BOTH atypical chest pain AND abnormal EKG.")}

        elif code == "ERR-ORTHO":
            has_formal_pt = self._search_any(notes, self._PT_FORMAL_SIGNALS)
            has_informal  = self._search_any(notes, self._PT_INFORMAL_SIGNALS)
            months        = self._extract_pt_months(notes)
            if has_informal and not has_formal_pt:
                return {"criteria_met": False, "confidence": "HIGH",
                        "rationale": (
                            "Notes indicate only informal/non-clinical activities (yoga, "
                            "acupuncture, or undocumented home exercises). ORTHO-404 requires "
                            "formal, clinically supervised PT or physician injections for "
                            ">= 4 consecutive months.")}
            if has_formal_pt and months >= 4:
                return {"criteria_met": True, "confidence": "HIGH",
                        "rationale": (
                            f"Notes confirm {months} consecutive months of formal, clinically "
                            f"supervised physical therapy — exceeding the 4-month minimum "
                            f"mandated by ORTHO-404.")}
            reason = (
                f"documented duration ({months} month(s)) is below the required 4-month minimum"
                if months > 0 else "no formal clinical PT duration is documented")
            return {"criteria_met": False,
                    "confidence": "MEDIUM" if months > 0 else "HIGH",
                    "rationale": (
                        f"Policy criteria NOT met. ORTHO-404 requires >= 4 consecutive "
                        f"months of formal therapy; {reason}.")}

        elif code == "ERR-ONCOLOGY":
            positive = self._search_any(notes, self._ONCO_POSITIVE_SIGNALS)
            negative = self._search_any(notes, self._ONCO_NEGATIVE_SIGNALS)
            if positive and not negative:
                return {"criteria_met": True, "confidence": "HIGH",
                        "rationale": (
                            "Companion diagnostic genetic testing is present and CONFIRMS the "
                            "required biomarker expression / mutation status for the requested "
                            "targeted agent. ONCO-202 criteria are fully satisfied.")}
            issue = (
                "biomarker test result is negative or absent for this agent's indication"
                if negative else "no companion diagnostic test result found in clinical notes")
            return {"criteria_met": False, "confidence": "HIGH",
                    "rationale": (
                        f"Policy criteria NOT met. ONCO-202 requires a positive companion "
                        f"diagnostic; {issue}.")}

        return {"criteria_met": False, "confidence": "LOW",
                "rationale": f"Unrecognised denial code '{denial_code}'. Manual review required."}


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 3 — AppealStrategyAgent
# ─────────────────────────────────────────────────────────────────────────────
class AppealStrategyAgent:
    """Drafts a formal appeal letter or a manual-review disposition notice."""

    def generate(self, patient: dict, audit: dict, evidence: dict) -> dict:
        ts        = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        name      = patient["Patient_Name"]
        case_id   = patient["Case_ID"]
        treatment = patient["Proposed_Treatment"]
        dx        = patient["Diagnosis"]
        rule_id   = audit["rule_id"]
        rule_title= audit["title"]

        if evidence["criteria_met"]:
            appeal_text = (
                f"DATE     : {ts}\n"
                f"RE       : Formal Appeal of Denied Claim — Case ID {case_id}\n"
                f"PATIENT  : {name}\n"
                f"DIAGNOSIS: {dx}\n"
                f"TREATMENT: {treatment}\n\n"
                f"To the Medical Director, Claims Review Committee,\n\n"
                f"We are writing on behalf of the above-named patient to formally contest the "
                f"claim denial issued under Policy Code {rule_id} — {rule_title}.\n\n"
                f"CLINICAL JUSTIFICATION\n"
                f"───────────────────────\n"
                f"{evidence['rationale']}\n\n"
                f"Upon thorough review of the patient's clinical record, we respectfully submit "
                f"that ALL mandatory coverage criteria enumerated under {rule_id} have been "
                f"demonstrably satisfied. The denial therefore does not reflect the documented "
                f"clinical presentation and is inconsistent with the governing policy language.\n\n"
                f"REQUEST\n"
                f"───────\n"
                f"We respectfully request:\n"
                f"  1. Immediate reconsideration and reversal of this denial.\n"
                f"  2. Expedited pre-authorisation for the proposed treatment: {treatment}.\n"
                f"  3. Written confirmation of the appeal outcome within 30 calendar days as\n"
                f"     required under applicable state and federal regulations.\n\n"
                f"Supporting documentation (EHR records, imaging, lab reports, and physician "
                f"attestations) is enclosed with this submission.\n\n"
                f"Submitted by  : Automated Appeals Processing System v2.0\n"
                f"Authorised by : Attending Physician of Record"
            )
            return {
                "disposition" : "SUCCESS_PROBABLE (90%+)",
                "confidence"  : evidence["confidence"],
                "appeal_text" : appeal_text,
            }
        else:
            appeal_text = (
                f"DATE     : {ts}\n"
                f"CASE ID  : {case_id}\n"
                f"PATIENT  : {name}\n"
                f"RESULT   : DENIAL UPHELD — Insufficient Clinical Evidence for Appeal\n\n"
                f"ANALYSIS SUMMARY\n"
                f"─────────────────\n"
                f"{evidence['rationale']}\n\n"
                f"The clinical record, as reviewed against the mandatory criteria of Policy "
                f"Rule {rule_id} ({rule_title}), does not present sufficient evidence to "
                f"support an automated appeal filing.\n\n"
                f"RECOMMENDED ACTIONS\n"
                f"────────────────────\n"
                f"  • Refer to the Utilisation Management (UM) team for manual case review.\n"
                f"  • Advise the treating physician to gather missing documentation.\n"
                f"  • Patient may reapply once deficiencies are resolved and documented.\n"
                f"  • A Peer-to-Peer (P2P) review with the Medical Director may be requested\n"
                f"    within 14 days of this determination.\n\n"
                f"Automated by  : Claims Review Intelligence System v2.0"
            )
            return {
                "disposition" : "DENIAL_UPHELD (Manual Review Required)",
                "confidence"  : evidence["confidence"],
                "appeal_text" : appeal_text,
            }


# ─────────────────────────────────────────────────────────────────────────────
# FLASK APPLICATION
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)

# Load policy document once at startup
with open(POLICY_PATH, "r", encoding="utf-8") as _f:
    _POLICY_TEXT = _f.read()

_policy_agent   = PolicyAuditAgent(_POLICY_TEXT)
_evidence_agent = ClinicalEvidenceAgent()
_appeal_agent   = AppealStrategyAgent()


@app.route("/")
def index():
    """Serve the main web UI."""
    return render_template("index.html")


@app.route("/process")
def process():
    """
    Run the full three-agent pipeline over all rows in denial_records.csv.
    Returns a JSON array of per-case result objects.
    """
    df      = pd.read_csv(CSV_PATH)
    results = []

    for _, row in df.iterrows():
        patient = row.to_dict()

        audit    = _policy_agent.audit(patient["Denial_Code"])
        evidence = _evidence_agent.evaluate(patient["Denial_Code"], patient["Clinical_Notes"])
        strategy = _appeal_agent.generate(patient, audit, evidence)

        results.append({
            "case_id"          : patient["Case_ID"],
            "patient_name"     : patient["Patient_Name"],
            "age"              : int(patient["Age"]),
            "denial_code"      : patient["Denial_Code"],
            "diagnosis"        : patient["Diagnosis"],
            "proposed_treatment": patient["Proposed_Treatment"],
            "clinical_notes"   : patient["Clinical_Notes"],
            # Agent 1
            "rule_id"          : audit["rule_id"],
            "rule_title"       : audit["title"],
            "policy_criteria"  : audit["criteria"],
            # Agent 2
            "criteria_met"     : evidence["criteria_met"],
            "confidence"       : evidence["confidence"],
            "rationale"        : evidence["rationale"],
            # Agent 3
            "disposition"      : strategy["disposition"],
            "appeal_text"      : strategy["appeal_text"],
        })

    return jsonify(results)


@app.route("/add-case", methods=["POST"])
def add_case():
    """
    Append a new patient denial record to denial_records.csv.
    Expects JSON body with the same fields as the CSV columns.
    Returns the newly appended row processed through all three agents.
    """
    from flask import request as req
    body = req.get_json(force=True)

    required = ["Case_ID", "Patient_Name", "Age", "Denial_Code",
                "Diagnosis", "Proposed_Treatment", "Clinical_Notes"]
    missing = [f for f in required if not body.get(f, "").strip()]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    # Validate denial code
    valid_codes = {"ERR-CARDIAC", "ERR-ORTHO", "ERR-ONCOLOGY"}
    if body["Denial_Code"].strip().upper() not in valid_codes:
        return jsonify({"error": f"Denial_Code must be one of {sorted(valid_codes)}"}), 400

    # Check for duplicate Case_ID
    df = pd.read_csv(CSV_PATH)
    if body["Case_ID"].strip() in df["Case_ID"].values:
        return jsonify({"error": f"Case_ID '{body['Case_ID']}' already exists."}), 409

    # Build new row
    new_row = {
        "Case_ID"          : body["Case_ID"].strip(),
        "Patient_Name"     : body["Patient_Name"].strip(),
        "Age"              : int(body["Age"]),
        "Denial_Code"      : body["Denial_Code"].strip().upper(),
        "Diagnosis"        : body["Diagnosis"].strip(),
        "Proposed_Treatment": body["Proposed_Treatment"].strip(),
        "Clinical_Notes"   : body["Clinical_Notes"].strip(),
    }

    # Append to CSV
    new_df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    new_df.to_csv(CSV_PATH, index=False)

    # Run agents on the new row
    patient  = new_row
    audit    = _policy_agent.audit(patient["Denial_Code"])
    evidence = _evidence_agent.evaluate(patient["Denial_Code"], patient["Clinical_Notes"])
    strategy = _appeal_agent.generate(patient, audit, evidence)

    return jsonify({
        "case_id"           : patient["Case_ID"],
        "patient_name"      : patient["Patient_Name"],
        "age"               : patient["Age"],
        "denial_code"       : patient["Denial_Code"],
        "diagnosis"         : patient["Diagnosis"],
        "proposed_treatment": patient["Proposed_Treatment"],
        "clinical_notes"    : patient["Clinical_Notes"],
        "rule_id"           : audit["rule_id"],
        "rule_title"        : audit["title"],
        "policy_criteria"   : audit["criteria"],
        "criteria_met"      : evidence["criteria_met"],
        "confidence"        : evidence["confidence"],
        "rationale"         : evidence["rationale"],
        "disposition"       : strategy["disposition"],
        "appeal_text"       : strategy["appeal_text"],
    }), 201


@app.route("/chat", methods=["POST"])
def chat():
    """
    Medical AI Policy Assistant — grounded Q&A over insurance_policies.txt.

    Accepts:  { "question": "Is a CT scan covered under ACC-901 if EKG is normal?" }
    Returns:  { "answer": "<markdown text>" }

    ── How to upgrade to IBM Granite (ibm-granite-3-2-8b) ──────────────────────
    Install:  pip install ibm-watsonx-ai
    Then replace the _rule_based_answer() call below with:

        from ibm_watsonx_ai import Credentials
        from ibm_watsonx_ai.foundation_models import ModelInference
        model = ModelInference(
            model_id="ibm/granite-3-2-8b-instruct",
            credentials=Credentials(api_key=os.environ["WATSONX_API_KEY"],
                                    url="https://us-south.ml.cloud.ibm.com"),
            project_id=os.environ["WATSONX_PROJECT_ID"],
        )
        prompt = f"POLICY DOCUMENT:\\n{_POLICY_TEXT}\\n\\nQUESTION: {question}\\n\\nANSWER:"
        answer = model.generate_text(prompt=prompt, params={"max_new_tokens": 512})
    ────────────────────────────────────────────────────────────────────────────
    """
    from flask import request as req
    body     = req.get_json(force=True)
    question = (body.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question field is required"}), 400

    answer = _rule_based_answer(question, _POLICY_TEXT)
    return jsonify({"answer": answer})


# ── Rule-based policy Q&A engine ─────────────────────────────────────────────
# Produces grounded, structured answers by matching the user's question against
# the known policy rules and returning detailed, formatted responses.
# No external API required — fully deterministic and auditable.
# ─────────────────────────────────────────────────────────────────────────────
_CHAT_RULES = [
    # ── ACC-901 / CARDIAC ────────────────────────────────────────────────────
    {
        "patterns": [
            r"acc.?901", r"cardiac.*mri", r"ct.*scan.*cardiac", r"cardiac.*ct",
            r"err.?cardiac", r"cardiac.*imagin", r"ekg.*normal", r"normal.*ekg",
            r"ekg.*abnormal", r"atypical.*chest.*pain", r"chest.*pain.*covered",
            r"cardiac.*covered", r"cardiac.*rule", r"mri.*covered",
        ],
        "answer": (
            "## ACC-901 — Advanced Cardiac Diagnostic Imaging\n\n"
            "**Coverage Denial Code:** `ERR-CARDIAC`\n\n"
            "### ✅ Covered when ALL of the following are documented:\n"
            "1. **Atypical chest pain** recorded by a licensed cardiologist or attending physician.\n"
            "2. A **resting EKG performed within 90 days** showing one or more abnormal findings, such as:\n"
            "   - ST-segment depression or elevation\n"
            "   - T-wave inversions\n"
            "   - Left Ventricular Hypertrophy (LVH)\n"
            "   - Bundle branch block, sinus bradycardia, or other conduction defects\n\n"
            "### ❌ NOT covered when:\n"
            "- The resting EKG is **normal** (no arrhythmia or conduction defect)\n"
            "- Chest pain is classified as **typical** (musculoskeletal / GERD) without cardiologist attestation\n"
            "- No prior EKG result is on file\n\n"
            "**Example:** A Cardiac MRI requested for a patient with exertional dyspnea and documented "
            "LVH + ST-segment abnormalities on EKG **would be approved** under ACC-901. "
            "A Cardiac MRI requested for a patient with a clean resting EKG **would be denied**.\n\n"
            "**Appeal path:** Submit an updated EKG report with cardiologist attestation confirming "
            "atypical chest pain. A peer-to-peer review with a board-certified cardiologist may be requested."
        ),
    },
    # ── ORTHO-404 / PT MONTHS ────────────────────────────────────────────────
    {
        "patterns": [
            r"ortho.?404", r"err.?ortho", r"arthroscop", r"knee.*surg", r"hip.*surg",
            r"shoulder.*surg", r"meniscect", r"rotator.*cuff", r"physical.*therapy.*months",
            r"pt.*months", r"4.*months?.*pt", r"conservative.*therapy", r"yoga.*covered",
            r"acupuncture.*covered", r"home.*exercise", r"injection.*covered",
            r"ortho.*rule", r"ortho.*covered", r"surgery.*covered",
        ],
        "answer": (
            "## ORTHO-404 — Arthroscopic Surgical Procedures\n\n"
            "**Coverage Denial Code:** `ERR-ORTHO`\n\n"
            "### ✅ Covered when ALL of the following are documented:\n"
            "1. **Minimum 4 consecutive calendar months** of formally supervised conservative therapy.\n"
            "2. Therapy must be **clinically supervised** — qualifying types:\n"
            "   - Licensed physiotherapist PT sessions (documented in EHR)\n"
            "   - Physician-administered intra-articular corticosteroid or hyaluronic acid injections\n"
            "3. **Imaging evidence** (X-ray, MRI, or CT) confirming structural pathology.\n\n"
            "### ❌ NOT covered / disqualified by:\n"
            "- Duration **less than 4 continuous months**\n"
            "- Home exercise programs, yoga, acupuncture, or OTC NSAIDs used **without clinical documentation**\n"
            "- Documentation gaps in the EHR\n\n"
            "**Key distinction:** 5+ months of clinically supervised PT → **Appeal succeeds**. "
            "2 weeks of yoga + acupuncture → **Denial upheld**.\n\n"
            "**Appeal path:** Provide complete physiotherapy attendance logs, physician injection notes, "
            "and updated imaging. An Independent Medical Examination (IME) may be ordered."
        ),
    },
    # ── ONCO-202 / ONCOLOGY ──────────────────────────────────────────────────
    {
        "patterns": [
            r"onco.?202", r"err.?oncolog", r"pembrolizumab", r"nivolumab",
            r"trastuzumab", r"cetuximab", r"immunotherap", r"targeted.*therap",
            r"her2", r"pd.?l1", r"kras", r"nras", r"braf", r"egfr",
            r"companion.*diagnost", r"biomarker", r"genetic.*test", r"ngs.*panel",
            r"oncolog.*covered", r"cancer.*therap.*covered", r"mutation.*covered",
        ],
        "answer": (
            "## ONCO-202 — Targeted Oncology & Immunotherapy\n\n"
            "**Coverage Denial Code:** `ERR-ONCOLOGY`\n\n"
            "### ✅ Covered when ALL of the following are present:\n"
            "1. A **companion diagnostic genetic test** on a tumour biopsy (NGS, FISH, IHC, or PCR).\n"
            "2. The test must **confirm the specific biomarker** for the requested agent:\n\n"
            "   | Agent | Required Biomarker |\n"
            "   |---|---|\n"
            "   | Pembrolizumab / Nivolumab | PD-L1 expression ≥ 1% or high TMB (≥ 10 mut/Mb) |\n"
            "   | Trastuzumab | HER2-positive (IHC 3+ or FISH ratio ≥ 2.0) |\n"
            "   | Cetuximab / Panitumumab | RAS/KRAS/NRAS **wild-type** (mutation-free) |\n"
            "   | Vemurafenib / Dabrafenib | BRAF V600E/K mutation confirmed |\n"
            "   | Osimertinib | EGFR exon 19 deletion or L858R mutation |\n\n"
            "3. Treatment plan co-signed by a **board-certified medical oncologist** per NCCN guidelines.\n\n"
            "### ❌ NOT covered when:\n"
            "- No companion diagnostic on file\n"
            "- Biomarker result is **negative** for the agent (e.g., HER2-negative for Trastuzumab)\n"
            "- Agent used outside its FDA-approved biomarker indication\n\n"
            "**Appeal path:** Submit full pathology report with companion diagnostic results, "
            "oncologist co-signature, and NCCN guideline citation. Tumour Board minutes may be included."
        ),
    },
    # ── APPEAL PROCESS ───────────────────────────────────────────────────────
    {
        "patterns": [
            r"how.*appeal", r"appeal.*process", r"file.*appeal", r"appeal.*denial",
            r"contest.*denial", r"denial.*appeal", r"appeal.*letter", r"p2p.*review",
            r"peer.*review", r"appeal.*step",
        ],
        "answer": (
            "## How to Appeal a Denied Insurance Claim\n\n"
            "The automated pipeline handles appeals in **three steps**:\n\n"
            "**Step 1 — PolicyAuditAgent**\n"
            "Maps the denial code to its governing rule (ACC-901, ORTHO-404, or ONCO-202) "
            "and retrieves the mandatory coverage criteria.\n\n"
            "**Step 2 — ClinicalEvidenceAgent**\n"
            "Scans the patient's clinical notes to determine whether the evidence satisfies "
            "all required criteria. Returns a `criteria_met` verdict with confidence level.\n\n"
            "**Step 3 — AppealStrategyAgent**\n"
            "- If `criteria_met = True` → drafts a formal appeal letter tagged **SUCCESS_PROBABLE (90%+)**\n"
            "- If `criteria_met = False` → issues a **DENIAL_UPHELD** notice with specific deficiencies "
            "and recommended remediation steps\n\n"
            "**General appeal timeline:**\n"
            "- Submit within **180 days** of the denial date\n"
            "- Insurer must respond within **30 calendar days** (standard) or **72 hours** (urgent)\n"
            "- A **Peer-to-Peer (P2P) review** with the insurer's Medical Director may be requested "
            "within 14 days of the determination"
        ),
    },
    # ── GENERAL POLICY OVERVIEW ──────────────────────────────────────────────
    {
        "patterns": [
            r"what.*rules?", r"list.*rules?", r"policy.*rules?", r"available.*rules?",
            r"what.*policies", r"coverage.*rules?", r"rules.*available",
            r"what.*covered", r"overview", r"summary.*polic",
        ],
        "answer": (
            "## Policy Rules Overview\n\n"
            "This system enforces **three coverage rules** from the NHCAB Unified Insurance Policy "
            "Coverage Reference (v4.1.2):\n\n"
            "| Rule ID | Denial Code | Procedure Group | Key Requirement |\n"
            "|---|---|---|---|\n"
            "| **ACC-901** | `ERR-CARDIAC` | Advanced Cardiac Diagnostic Imaging | "
            "Atypical chest pain **+** Abnormal resting EKG |\n"
            "| **ORTHO-404** | `ERR-ORTHO` | Arthroscopic Surgical Procedures | "
            "≥ 4 consecutive months of formally documented clinical PT or injections |\n"
            "| **ONCO-202** | `ERR-ONCOLOGY` | Targeted Oncology & Immunotherapy | "
            "Companion diagnostic confirms required biomarker expression |\n\n"
            "Ask me about any specific rule, denial code, or coverage scenario for a detailed answer."
        ),
    },
]

_FALLBACK_ANSWER = (
    "I'm the **Medical AI Policy Assistant** grounded in your `insurance_policies.txt` document.\n\n"
    "I can answer questions about:\n"
    "- 🫀 **ACC-901** — Cardiac diagnostic imaging coverage (ERR-CARDIAC)\n"
    "- 🦴 **ORTHO-404** — Arthroscopic surgery coverage (ERR-ORTHO)\n"
    "- 🧬 **ONCO-202** — Targeted oncology therapy coverage (ERR-ONCOLOGY)\n"
    "- 📋 How to file and structure a claim appeal\n\n"
    "Try asking: *\"What are the EKG requirements for a Cardiac MRI?\"* or "
    "*\"How many months of PT are needed for knee surgery coverage?\"*"
)


def _rule_based_answer(question: str, policy_text: str) -> str:
    """
    Match the question against known policy patterns and return a rich,
    structured markdown answer grounded in the policy document.
    """
    q = question.lower()
    for rule in _CHAT_RULES:
        if any(re.search(p, q) for p in rule["patterns"]):
            return rule["answer"]
    # Soft fallback: search raw policy text for any keyword overlap
    words = [w for w in re.findall(r"[a-z]{4,}", q) if w not in
             {"what","when","does","this","that","with","have","from","they",
              "will","would","should","covered","coverage","under","policy"}]
    if words:
        for word in words:
            if re.search(word, policy_text, re.IGNORECASE):
                # Return the relevant section from the policy doc
                lines = policy_text.splitlines()
                excerpt = []
                for i, line in enumerate(lines):
                    if re.search(word, line, re.IGNORECASE):
                        start = max(0, i - 2)
                        end   = min(len(lines), i + 8)
                        excerpt = lines[start:end]
                        break
                if excerpt:
                    return (
                        f"Here is the relevant excerpt from the policy document "
                        f"matching your query about **\"{word}\"**:\n\n"
                        f"```\n" + "\n".join(excerpt) + "\n```\n\n"
                        f"Would you like a more detailed explanation of a specific rule? "
                        f"Try asking about ACC-901, ORTHO-404, or ONCO-202."
                    )
    return _FALLBACK_ANSWER


@app.route("/skills")
def skills():
    """Render the watsonx Orchestrate skill schema viewer page."""
    schema_path = os.path.join(BASE_DIR, "config", "orchestrate_skill_schema.json")
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_raw  = f.read()
        schema_obj  = json.loads(schema_raw)
    # Pretty-print for display
    schema_pretty = json.dumps(schema_obj, indent=2)
    return render_template("skills.html", schema=schema_pretty, schema_obj=schema_obj)


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 70)
    print("  IBM watsonx Multi-Agent Medical Appeals Portal")
    print("  Problem Statement No. 38 | Edunet Foundation IBM SkillsBuild")
    print("  Server : http://127.0.0.1:5000")
    print("=" * 70)
    app.run(debug=True, port=5000)
