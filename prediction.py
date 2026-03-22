"""
Phase 2 — Prediction Engine
=============================
Built specifically for the ITSM_data.csv dataset columns:
  CI_Name, CI_Cat, CI_Subcat, WBS, Incident_ID, Status,
  Impact, Urgency, Priority, Category, Alert_Status,
  No_of_Reassignments, Open_Time, Handle_Time_hrs, Closure_Code, etc.

Pipeline per ticket:
  1. Keyword anomaly detection (instant, no API)
  2. Groq LLaMA 3.3-70B zero-shot classification (JSON mode)
  3. Trust calibration from historical fix_outcomes table
  4. Approval path routing (A / B / C)
  5. DB write + audit log

Run standalone test:
  python prediction.py
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

DB_PATH    = "db/opsai.db"
GROQ_MODEL = "llama-3.3-70b-versatile"   # Updated from 3.1 — current best free model

# ── Lazy Groq client (only imports if key is present) ─────────────────────────
_groq_client = None

def get_groq():
    global _groq_client
    if _groq_client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GROQ_API_KEY missing.\n"
                "1. Go to https://console.groq.com → API Keys → Create\n"
                "2. Copy the key (starts with gsk_)\n"
                "3. Paste into your .env file: GROQ_API_KEY=gsk_xxx\n"
                "4. Re-run"
            )
        from groq import Groq
        _groq_client = Groq(api_key=api_key)
    return _groq_client


# ── ITSM-specific keyword rules (mapped to actual dataset fields) ──────────────

# These map CI_Cat values from your dataset to risk signals
CI_CAT_RISK = {
    "storage":        "P2",   # was 'database' in notebook — remapped
    "application":    "P2",
    "subapplication": "P2",
    "hardware":       "P1",   # physical failures escalate fast
    "network":        "P1",
}

# Alert_Status values from dataset
ALERT_STATUS_ESCALATION = {
    "true":  "P1",    # active alert = critical
    "false": None,    # no escalation
}

P1_KEYWORDS = [
    "production down", "all users", "data loss", "complete outage",
    "critical failure", "unresponsive", "breach", "corrupted",
    "all services", "entire system", "site down", "p1", "priority 1",
    "severity 1", "sev1",
]
P2_KEYWORDS = [
    "degraded", "slow", "intermittent", "partial", "some users",
    "timeout", "high cpu", "memory leak", "disk full", "connection refused",
    "failing", "p2", "priority 2",
]
SECURITY_KEYWORDS = [
    "unauthorized", "breach", "injection", "exploit", "malware",
    "ransomware", "suspicious", "intrusion", "security",
]


def run_keyword_analysis(description: str, ci_cat: str = "", alert_status: str = "") -> dict:
    """
    Layer 1: Deterministic anomaly detection.
    Uses description text + ITSM dataset fields (CI_Cat, Alert_Status).
    No API call — instant, always works.
    """
    desc  = description.lower()
    flags = []
    suggested = None

    # Check Alert_Status from dataset
    if alert_status.lower() in ALERT_STATUS_ESCALATION:
        if ALERT_STATUS_ESCALATION[alert_status.lower()]:
            flags.append(f"ACTIVE_ALERT: Alert_Status={alert_status}")
            suggested = "P1"

    # Check CI_Cat from dataset
    ci_risk = CI_CAT_RISK.get(ci_cat.lower())
    if ci_risk and not suggested:
        flags.append(f"CI_CAT_RISK: {ci_cat} → {ci_risk}")
        suggested = ci_risk

    # Keyword scan
    for kw in P1_KEYWORDS:
        if kw in desc:
            flags.append(f"P1_SIGNAL: '{kw}'")
            suggested = "P1"
            break

    if suggested != "P1":
        for kw in P2_KEYWORDS:
            if kw in desc:
                flags.append(f"P2_SIGNAL: '{kw}'")
                if not suggested:
                    suggested = "P2"
                break

    for kw in SECURITY_KEYWORDS:
        if kw in desc:
            flags.append(f"SECURITY_FLAG: '{kw}'")
            suggested = "P1"   # Security always P1

    return {
        "flags":              flags,
        "suggested_severity": suggested,
        "anomaly_detected":   len(flags) > 0,
    }


# ── The zero-shot classification prompt ──────────────────────────────────────

def build_prompt(description: str, ci_cat: str, ci_subcat: str,
                 category: str, urgency: str, impact: str) -> str:
    """
    Builds the Groq prompt with full ITSM context.
    Uses actual column values from the dataset for richer classification.
    """
    context_parts = []
    if ci_cat:     context_parts.append(f"CI Category: {ci_cat}")
    if ci_subcat:  context_parts.append(f"CI Sub-category: {ci_subcat}")
    if category:   context_parts.append(f"Incident Category: {category}")
    if urgency:    context_parts.append(f"Urgency (from ticket): {urgency}")
    if impact:     context_parts.append(f"Impact (from ticket): {impact}")

    context_block = "\n".join(context_parts) if context_parts else "No additional context"

    return f"""You are a senior IT operations analyst. Classify this ITSM incident ticket accurately.

TICKET CONTEXT:
{context_block}

TICKET DESCRIPTION:
{description}

SEVERITY RULES (pick exactly one):
- P1 = Production down, ALL users affected, data loss risk, complete outage, active security breach
- P2 = Partial degradation, SOME users affected, service slow or intermittent, workaround exists
- P3 = Single user, cosmetic issue, scheduled maintenance, low business impact, can wait

RISK TIER RULES (must match severity):
- Critical = P1 tickets only, OR any ticket with confirmed security breach or data exposure
- Medium   = P2 tickets, OR P3 tickets where the fix has side effects or requires service restart
- Low      = P3 tickets with safe, reversible fixes (certificate renewal, config change, cache clear)

CONFIDENCE RULES:
- Give 85-95 when description clearly matches one category and fix is obvious
- Give 70-84 when description is clear but fix has some uncertainty  
- Give 55-69 when description is vague or could mean multiple things
- Give below 55 only when you genuinely cannot classify

CRITICAL: Do NOT assign Critical risk to P2 or P3 tickets. Match risk tier to severity.

Respond ONLY with valid JSON. No markdown. No preamble. No extra text.

{{
  "severity": "P1 or P2 or P3",
  "category": "Database or Network or Authentication or Infrastructure or Application or General",
  "predicted_incident_type": "short label e.g. DB Connection Failure",
  "anomaly_flagged": true or false,
  "confidence_score": integer 0-100,
  "risk_tier": "Low or Medium or Critical",
  "recommended_fix": "one concrete first action",
  "reasoning": "one sentence explaining severity and risk tier choice"
}}"""


# ── Parse + validate LLM JSON response ───────────────────────────────────────

def parse_response(raw: str) -> dict:
    text = raw.strip()

    # Strip markdown fences if model adds them
    if "```" in text:
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else parts[0]
        if text.startswith("json"):
            text = text[4:]

    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1:
        raise ValueError(f"No JSON in response: {raw[:150]}")

    p = json.loads(text[start:end])

    # Normalize and validate all fields
    sev = str(p.get("severity", "P3")).upper()
    p["severity"] = sev if sev in ("P1","P2","P3") else "P3"

    cat = str(p.get("category","General")).strip().title()
    valid_cats = {"Database","Network","Authentication","Infrastructure","Application","General"}
    p["category"] = cat if cat in valid_cats else "General"

    tier = str(p.get("risk_tier","Medium")).strip().title()
    p["risk_tier"] = tier if tier in ("Low","Medium","Critical") else {
        "P1":"Critical","P2":"Medium","P3":"Low"
    }.get(p["severity"],"Medium")

    score = p.get("confidence_score", 70)
    p["confidence_score"]    = max(0, min(100, int(score)))
    p["anomaly_flagged"]     = bool(p.get("anomaly_flagged", False))
    p["predicted_incident_type"] = str(p.get("predicted_incident_type","Unknown"))
    p["recommended_fix"]     = str(p.get("recommended_fix","Investigate and escalate"))
    p["reasoning"]           = str(p.get("reasoning",""))

    return p


# ── Trust calibration: adjusts raw LLM confidence with outcome history ────────

def calibrate_confidence(raw: int, category: str, severity: str, conn) -> int:
    """
    Adjusts confidence using historical fix outcome data from DB.
    No history   → trust Groq score with minimal deflation to preserve path diversity
    Good history → blend LLM score with historical accuracy
    Rollbacks    → penalise (-10 per rollback)
    P1           → cap at 80 so human always involved
    P2           → cap at 90, allow Path B
    P3           → no cap, allow Path A if score is high enough
    """
    rows = conn.execute("""
        SELECT approve_count, reject_count, rollback_count, total_actions
        FROM   fix_outcomes WHERE category = ?
    """, (category,)).fetchall()

    total_history = sum(r[3] for r in rows) if rows else 0

    if total_history < 5:
        # No history — deflate slightly (10%) to be conservative but not over-restrict
        calibrated = int(raw * 0.90)
    else:
        total_approvals  = sum(r[0] for r in rows)
        total_actions    = sum(r[3] for r in rows)
        total_rollbacks  = sum(r[2] for r in rows)
        hist_accuracy    = total_approvals / total_actions
        rollback_penalty = total_rollbacks * 10
        calibrated = int((raw * 0.4) + (hist_accuracy * 100 * 0.6) - rollback_penalty)

    # Severity-based caps — P1 always needs human, P2/P3 can auto-route
    if severity == "P1":
        calibrated = min(calibrated, 80)   # P1 max 80% → always Path C or B
    elif severity == "P2":
        calibrated = min(calibrated, 90)   # P2 max 90% → Path B or A
    # P3 uncapped → can reach Path A if Groq is confident

    return max(0, min(100, calibrated))


# ── Approval path from calibrated confidence + risk tier ─────────────────────

def get_approval_path(confidence: int, risk_tier: str, severity: str) -> str:
    """
    Path A = Auto-execute (10s cancel window) — P3, low risk, high confidence
    Path B = Single operator approve/reject — P2 with decent confidence, or medium risk
    Path C = Mandatory senior review — P1 always, critical+low confidence, or very low confidence
    """
    # P1 is always Path C — non-negotiable
    if severity == "P1":
        return "C"

    # Very low confidence → require senior review
    if confidence < 40:
        return "C"

    # Critical risk: only force C when confidence is also low (<70)
    if risk_tier == "Critical" and confidence < 70:
        return "C"

    # P3 with high confidence and explicitly low risk → auto-execute
    if severity == "P3" and risk_tier == "Low" and confidence >= 75:
        return "A"

    # P3 with decent confidence → single operator approval
    if severity == "P3" and confidence >= 65:
        return "B"

    # P2 with decent confidence → single operator approval
    if severity == "P2" and confidence >= 60:
        return "B"

    # P2 with lower confidence → require review
    if severity == "P2":
        return "C"

    # P3 fallback
    return "B"


# ── Keyword fallback (if Groq is down or key missing) ────────────────────────

def keyword_fallback(ticket_id: str, pid: str, description: str,
                     category_hint: str, error: str) -> dict:
    desc = description.lower()
    sev  = "P1" if any(k in desc for k in P1_KEYWORDS) else \
           "P2" if any(k in desc for k in P2_KEYWORDS) else "P3"
    tier = {"P1":"Critical","P2":"Medium","P3":"Low"}[sev]

    return {
        "id": pid, "ticket_id": ticket_id,
        "predicted_severity":      sev,
        "predicted_category":      category_hint or "General",
        "predicted_incident_type": "Unknown (fallback mode)",
        "confidence_score":        40,
        "raw_confidence":          40,
        "risk_tier":               tier,
        "anomaly_flagged":         sev == "P1",
        "recommended_fix":         "Escalate to on-call engineer immediately",
        "reasoning":               f"Keyword fallback — Groq unavailable: {error[:80]}",
        "approval_path":           "C",   # Always force human in fallback
        "raw_llm_response":        None,
        "model_used":              "keyword_fallback",
        "status":                  "fallback",
    }


# ── Main function — call this from ingestion.py ───────────────────────────────

def predict_ticket(
    ticket_id:    str,
    description:  str,
    category_hint: str = None,
    ci_cat:       str  = "",
    ci_subcat:    str  = "",
    category:     str  = "",
    urgency:      str  = "",
    impact:       str  = "",
    alert_status: str  = "",
) -> dict:
    """
    Full Phase 2 prediction pipeline.

    Args:
        ticket_id:     ID already stored in tickets table
        description:   Ticket text (CI_Name or synthesized description)
        category_hint: Category from Phase 1 keyword detection
        ci_cat:        CI_Cat column from ITSM dataset
        ci_subcat:     CI_Subcat column from ITSM dataset
        category:      Category column from ITSM dataset
        urgency:       Urgency column from ITSM dataset
        impact:        Impact column from ITSM dataset
        alert_status:  Alert_Status column from ITSM dataset

    Returns:
        dict with full prediction including approval_path
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    pid  = str(uuid.uuid4())

    # ── Layer 1: Keyword analysis (always runs, no API) ───────────────────────
    kw_analysis = run_keyword_analysis(description, ci_cat, alert_status)

    result = None

    try:
        # ── Layer 2: Groq zero-shot classification ────────────────────────────
        client = get_groq()
        prompt = build_prompt(description, ci_cat, ci_subcat, category, urgency, impact)

        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an IT operations AI. "
                        "Respond with valid JSON only. "
                        "Never add markdown, preamble, or explanation."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,    # Low = consistent, deterministic
            max_tokens=350,
        )

        raw = response.choices[0].message.content
        parsed = parse_response(raw)

        # If keyword layer detected higher severity — respect it
        kw_sev = kw_analysis["suggested_severity"]
        sev_rank = {"P1":1,"P2":2,"P3":3}
        final_sev = parsed["severity"]
        if kw_sev and sev_rank.get(kw_sev,3) < sev_rank.get(parsed["severity"],3):
            final_sev = kw_sev
            parsed["reasoning"] += f" (escalated by keyword: {kw_analysis['flags'][0]})"

        # ── Layer 3: Trust calibration ─────────────────────────────────────────
        calibrated = calibrate_confidence(
            parsed["confidence_score"], parsed["category"], final_sev, conn
        )

        # ── Layer 4: Approval path ─────────────────────────────────────────────
        path = get_approval_path(calibrated, parsed["risk_tier"], final_sev)

        result = {
            "id":                      pid,
            "ticket_id":               ticket_id,
            "predicted_severity":      final_sev,
            "predicted_category":      parsed["category"],
            "predicted_incident_type": parsed["predicted_incident_type"],
            "confidence_score":        calibrated,
            "raw_confidence":          parsed["confidence_score"],
            "risk_tier":               parsed["risk_tier"],
            "anomaly_flagged":         parsed["anomaly_flagged"] or kw_analysis["anomaly_detected"],
            "recommended_fix":         parsed["recommended_fix"],
            "reasoning":               parsed["reasoning"],
            "approval_path":           path,
            "keyword_flags":           kw_analysis["flags"],
            "raw_llm_response":        raw,
            "model_used":              GROQ_MODEL,
            "status":                  "success",
        }

    except Exception as e:
        print(f"⚠️  Groq failed for {ticket_id}: {e}")
        print("   → Falling back to keyword classification (Path C forced)")
        result = keyword_fallback(ticket_id, pid, description, category_hint, str(e))
        result["keyword_flags"] = kw_analysis["flags"]

    # ── Layer 5: Persist to DB ────────────────────────────────────────────────
    try:
        conn.execute("""
            INSERT INTO predictions
            (id, ticket_id, predicted_severity, predicted_category,
             predicted_incident, confidence_score, risk_tier,
             anomaly_flagged, reasoning, raw_llm_response, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            result["id"], result["ticket_id"],
            result["predicted_severity"], result["predicted_category"],
            result["predicted_incident_type"], result["confidence_score"],
            result["risk_tier"], int(result["anomaly_flagged"]),
            result["reasoning"], result.get("raw_llm_response",""),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

        # Escalate severity in tickets table only from P3 (auto-detected default).
        # Do NOT override P2 or P1 — respect the user's explicit severity choice.
        existing = conn.execute(
            "SELECT severity FROM tickets WHERE id=?", (ticket_id,)
        ).fetchone()
        if existing:
            existing_sev = dict(existing).get("severity", "P3")
            rank = {"P1":1,"P2":2,"P3":3}
            predicted_rank = rank.get(result["predicted_severity"], 3)
            existing_rank  = rank.get(existing_sev, 3)
            # Only escalate when current severity is P3 (the default) and AI says higher
            if existing_sev == "P3" and predicted_rank < existing_rank:
                conn.execute(
                    "UPDATE tickets SET severity=? WHERE id=?",
                    (result["predicted_severity"], ticket_id)
                )

        # Audit log entry
        conn.execute("""
            INSERT INTO audit_log
            (id, event_type, ticket_id, confidence, risk_tier,
             action_taken, reasoning, outcome, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            str(uuid.uuid4()), "PREDICT", ticket_id,
            result["confidence_score"], result["risk_tier"],
            f"Classified {result['predicted_severity']} / {result['predicted_category']} "
            f"→ Approval Path {result['approval_path']}",
            result["reasoning"], result["status"],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

        conn.commit()
    except Exception as e:
        print(f"⚠️  DB write failed: {e}")
    finally:
        conn.close()

    return result


# ── Standalone test — run directly to verify Groq connection ─────────────────

if __name__ == "__main__":
    print("\n🧪 Phase 2 — Prediction Engine Test")
    print("=" * 55)

    # Simulated tickets using actual ITSM dataset field values
    TEST_CASES = [
        {
            "ticket_id":   "TEST001",
            "description": "Web Based Application completely down, all users locked out of the system",
            "ci_cat":      "application",
            "ci_subcat":   "Web Based Application",
            "category":    "incident",
            "urgency":     "1",
            "impact":      "1",
            "alert_status":"True",
        },
        {
            "ticket_id":   "TEST002",
            "description": "Storage subsystem showing intermittent errors on backup jobs",
            "ci_cat":      "storage",
            "ci_subcat":   "SAN Storage",
            "category":    "incident",
            "urgency":     "3",
            "impact":      "3",
            "alert_status":"False",
        },
        {
            "ticket_id":   "TEST003",
            "description": "Desktop application running slowly for one user in finance team",
            "ci_cat":      "subapplication",
            "ci_subcat":   "Desktop Application",
            "category":    "incident",
            "urgency":     "4",
            "impact":      "4",
            "alert_status":"False",
        },
    ]

    for tc in TEST_CASES:
        tid = tc.pop("ticket_id")
        desc = tc.pop("description")
        print(f"\n📋 {tid}: {desc[:60]}...")

        try:
            result = predict_ticket(ticket_id=tid, description=desc, **tc)
            sev_icon = {"P1":"🔴","P2":"🟡","P3":"🟢"}.get(result["predicted_severity"],"⚪")
            path_icon = {"A":"🟢 Auto","B":"🟡 Approval","C":"🔴 Senior Review"}.get(result["approval_path"],"?")

            print(f"  {sev_icon} Severity:   {result['predicted_severity']}")
            print(f"  📁 Category:   {result['predicted_category']}")
            print(f"  🎯 Incident:   {result['predicted_incident_type']}")
            print(f"  📊 Confidence: {result['confidence_score']}% (raw: {result.get('raw_confidence','?')}%)")
            print(f"  ⚠️  Risk Tier:  {result['risk_tier']}")
            print(f"  🛤️  Path:       {path_icon}")
            print(f"  🔧 Fix:        {result['recommended_fix']}")
            print(f"  💬 Reasoning:  {result['reasoning']}")
            if result.get("keyword_flags"):
                print(f"  🚩 KW Flags:  {result['keyword_flags']}")
            print(f"  ✅ Status:     {result['status']}")
        except Exception as e:
            print(f"  ❌ Error: {e}")
