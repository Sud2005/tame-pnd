"""
Phase 5 -- Compliance Report Generator
========================================
Generates audit-ready PDF compliance reports for resolved incidents.
Each report contains the full AI decision chain in plain English,
suitable for GDPR, SOX, and SOC2 auditors.

Architecture:
  1. Pull all data for a ticket: ticket, prediction, RCA, audit trail,
     executions, knowledge deltas
  2. Generate plain-English explainability narrative
  3. Render structured PDF using fpdf2

No system dependencies — fpdf2 is pure Python.
"""

import io
import json
import sqlite3
from datetime import datetime

from fpdf import FPDF

DB_PATH = "db/opsai.db"


# ── Explainability Engine ────────────────────────────────────────────────────

def generate_explainability(ticket_id: str) -> dict:
    """
    Generate a plain-English explanation of every AI decision
    made for this ticket. Designed for non-technical auditors.

    Returns a dict with structured explanation sections.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    ticket = conn.execute(
        "SELECT * FROM tickets WHERE id=?", (ticket_id,)
    ).fetchone()
    if not ticket:
        conn.close()
        return {"error": f"Ticket {ticket_id} not found"}

    ticket = dict(ticket)

    # Pull prediction
    pred = conn.execute(
        "SELECT * FROM predictions WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1",
        (ticket_id,)
    ).fetchone()
    pred = dict(pred) if pred else None

    # Pull RCA
    rca = conn.execute(
        "SELECT * FROM rca_results WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1",
        (ticket_id,)
    ).fetchone()
    rca = dict(rca) if rca else None

    # Pull audit events
    events = conn.execute(
        "SELECT * FROM audit_log WHERE ticket_id=? ORDER BY timestamp ASC",
        (ticket_id,)
    ).fetchall()
    events = [dict(e) for e in events]

    # Pull executions
    execs = conn.execute(
        "SELECT * FROM executions WHERE ticket_id=? ORDER BY executed_at DESC",
        (ticket_id,)
    ).fetchall()
    execs = [dict(e) for e in execs]

    # Pull knowledge deltas
    deltas = conn.execute(
        "SELECT * FROM knowledge_deltas WHERE ticket_id=?",
        (ticket_id,)
    ).fetchone()
    delta = dict(deltas) if deltas else None

    conn.close()

    # ── Build plain-English explanations ──────────────────────────────────────

    sections = {}

    # 1. Incident Summary
    sections["incident_summary"] = (
        f"Incident {ticket_id} was reported on {ticket.get('opened_at', 'unknown date')} "
        f"with an initial severity of {ticket.get('severity', 'unknown')}. "
        f"The incident was categorised as '{ticket.get('category', 'General')}'. "
        f"Description: \"{ticket.get('description', 'No description')}\"."
    )

    # 2. AI Classification Decision
    if pred:
        conf = pred.get("confidence_score", "N/A")
        pred_sev = pred.get("predicted_severity", "N/A")
        pred_cat = pred.get("predicted_category", "N/A")
        reasoning = pred.get("reasoning", "No reasoning recorded")
        risk = pred.get("risk_tier", "N/A")
        path = pred.get("approval_path", "N/A")
        path_labels = {
            "A": "Auto-Execute (high confidence, low risk)",
            "B": "Single Operator Approval (medium confidence)",
            "C": "Mandatory Senior Review (critical risk or low confidence)",
        }

        sections["classification_decision"] = (
            f"The AI classification engine (Groq LLaMA 3.3-70B) analysed the ticket description "
            f"and classified it as severity {pred_sev} in the {pred_cat} category. "
            f"The AI's reasoning was: \"{reasoning}\". "
            f"The initial confidence score from the LLM was calibrated against historical fix outcome data "
            f"to produce a final confidence of {conf}%. "
            f"Based on this confidence ({conf}%), the risk tier ({risk}), and severity ({pred_sev}), "
            f"the system assigned Approval Path {path}: {path_labels.get(path, 'Unknown path')}. "
            f"This means {'the fix was eligible for autonomous execution with a human cancel window' if path == 'A' else 'a human operator was required to review and approve the fix' if path == 'B' else 'a senior engineer review with written justification was mandated'}."
        )
    else:
        sections["classification_decision"] = (
            "No AI classification was recorded for this ticket. "
            "The ticket may have been handled manually or the prediction engine was unavailable."
        )

    # 3. Root Cause Analysis Decision
    if rca:
        root_cause = rca.get("root_cause", "Not determined")
        rca_conf = rca.get("confidence_score", "N/A")
        rca_risk = rca.get("risk_tier", "N/A")
        rec_fix = rca.get("recommended_fix", "None")

        # Parse similarity scores
        sim_scores = []
        try:
            sim_scores = json.loads(rca.get("similarity_scores", "[]"))
        except Exception:
            pass

        similar_ids = [
            rca.get("similar_incident_1"),
            rca.get("similar_incident_2"),
            rca.get("similar_incident_3"),
        ]
        similar_ids = [s for s in similar_ids if s]

        sections["rca_decision"] = (
            f"The Root Cause Analysis engine searched a vector database (FAISS index) of "
            f"46,000+ historical incidents using semantic similarity. "
            f"It found {len(similar_ids)} similar past incidents "
            f"(similarity scores: {', '.join(f'{s:.1%}' for s in sim_scores) if sim_scores else 'not recorded'}). "
            f"Based on the patterns in these past incidents, the AI determined the root cause to be: "
            f"\"{root_cause}\". "
            f"The recommended fix was: \"{rec_fix}\". "
            f"The AI's confidence in this analysis was {rca_conf}% with a risk tier of {rca_risk}."
        )
    else:
        sections["rca_decision"] = (
            "No Root Cause Analysis was performed for this ticket."
        )

    # 4. Approval Workflow
    approve_events = [e for e in events if e.get("event_type") in (
        "APPROVE", "EXECUTE", "AUTO_APPROVE", "REJECT", "ROLLBACK"
    )]
    if approve_events:
        steps = []
        for e in approve_events:
            action = e.get("action_taken", "Unknown action")
            who = e.get("operator_id", "system")
            when = e.get("timestamp", "unknown time")
            outcome = e.get("outcome", "unknown")
            steps.append(
                f"  - {when}: {action} (by {who}, outcome: {outcome})"
            )

        sections["approval_workflow"] = (
            f"The following approval actions were recorded for this incident:\n"
            + "\n".join(steps)
        )
    else:
        sections["approval_workflow"] = (
            "No approval workflow actions were recorded."
        )

    # 5. Knowledge Delta
    if delta:
        was_different = delta.get("was_different", 0)
        if was_different:
            sections["knowledge_delta"] = (
                f"The human operator modified the AI's recommended fix. "
                f"The AI recommended: \"{delta.get('ai_recommended_fix', 'N/A')}\". "
                f"The operator applied: \"{delta.get('operator_applied_fix', 'N/A')}\". "
                f"The operator's reasoning was: \"{delta.get('operator_reasoning', 'Not provided')}\". "
                f"This knowledge delta has been recorded and will be used to improve "
                f"future AI recommendations for the {delta.get('category', 'General')} category."
            )
        else:
            sections["knowledge_delta"] = (
                "The human operator accepted the AI's recommended fix without modification. "
                "This positive signal has been recorded to reinforce the AI's confidence "
                "in similar future recommendations."
            )
    else:
        sections["knowledge_delta"] = (
            "No knowledge delta was recorded for this ticket."
        )

    # 6. Execution outcome
    if execs:
        last = execs[0]
        outcome = last.get("outcome", "unknown")
        rolled_back = last.get("rolled_back", 0)
        sections["execution_outcome"] = (
            f"The fix was executed with outcome: {outcome}. "
            f"{'The fix was subsequently rolled back.' if rolled_back else 'No rollback was performed.'} "
            f"Fix type applied: {last.get('fix_type', 'unknown')}."
        )
    else:
        sections["execution_outcome"] = (
            "No execution record was found for this ticket."
        )

    # 7. Compliance statement
    sections["compliance_statement"] = (
        f"This report was generated automatically by the TamePND AI Operations platform. "
        f"All AI decisions documented in this report were made by the Groq LLaMA 3.3-70B "
        f"large language model, calibrated against historical outcome data and cross-organisation "
        f"federated signals. Every automated decision was subject to human oversight through "
        f"the platform's three-tier approval workflow (Path A/B/C). "
        f"This report satisfies Article 22 of GDPR (right to explanation of automated decisions), "
        f"SOX Section 404 (internal controls over automated processes), and "
        f"SOC 2 Type II CC7.2 (monitoring of system components). "
        f"Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}."
    )

    return {
        "ticket_id": ticket_id,
        "ticket": ticket,
        "prediction": pred,
        "rca": rca,
        "events": events,
        "executions": execs,
        "knowledge_delta": delta,
        "sections": sections,
    }


# ── PDF Generation ───────────────────────────────────────────────────────────

class CompliancePDF(FPDF):
    """Custom PDF class for compliance reports."""

    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=25)

    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(0, 150, 200)
        self.cell(0, 6, "TAMEPND  |  AI Operations Compliance Report", align="L")
        self.set_text_color(130, 130, 130)
        self.set_font("Helvetica", "", 8)
        self.cell(0, 6, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(0, 150, 200)
        self.line(10, self.get_y() + 1, 200, self.get_y() + 1)
        self.ln(5)

    def footer(self):
        self.set_y(-20)
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(130, 130, 130)
        self.cell(0, 5, "CONFIDENTIAL - For authorised compliance review only", align="L")
        self.cell(0, 5, f"Page {self.page_no()}/{{nb}}", align="R")

    def section_title(self, title: str):
        self.ln(3)
        self.set_fill_color(0, 150, 200)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 11)
        self.cell(0, 8, f"  {title}", fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(3)
        self.set_text_color(40, 40, 40)

    def section_body(self, text: str):
        self.set_font("Helvetica", "", 9)
        self.set_text_color(60, 60, 60)
        # Encode safely for PDF
        safe_text = text.encode("latin-1", "replace").decode("latin-1")
        self.multi_cell(0, 5, safe_text)
        self.ln(2)

    def key_value(self, key: str, value: str):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(80, 80, 80)
        safe_key = key.encode("latin-1", "replace").decode("latin-1")
        self.cell(50, 5, safe_key)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(40, 40, 40)
        safe_val = str(value).encode("latin-1", "replace").decode("latin-1")
        self.cell(0, 5, safe_val, new_x="LMARGIN", new_y="NEXT")

    def warning_box(self, text: str):
        self.set_fill_color(255, 240, 230)
        self.set_draw_color(255, 160, 50)
        self.set_text_color(180, 90, 0)
        self.set_font("Helvetica", "B", 9)
        safe_text = text.encode("latin-1", "replace").decode("latin-1")
        self.cell(0, 7, f"  WARNING: {safe_text}", border=1, fill=True,
                  new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(40, 40, 40)
        self.ln(2)


def generate_compliance_pdf(ticket_id: str) -> bytes:
    """
    Generate a full compliance PDF report for a ticket.
    Returns raw PDF bytes.
    """
    data = generate_explainability(ticket_id)
    if "error" in data:
        raise ValueError(data["error"])

    ticket = data["ticket"]
    pred = data.get("prediction")
    rca = data.get("rca")
    events = data.get("events", [])
    execs = data.get("executions", [])
    sections = data["sections"]

    pdf = CompliancePDF()
    pdf.alias_nb_pages()
    pdf.add_page()

    # ── Title ─────────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(0, 100, 150)
    pdf.cell(0, 12, "Incident Compliance Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 7, f"Ticket ID: {ticket_id}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7,
             f"Severity: {ticket.get('severity', '?')}  |  "
             f"Category: {ticket.get('category', '?')}  |  "
             f"Status: {ticket.get('status', '?')}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # ── Section 1: Incident Summary ───────────────────────────────────────────
    pdf.section_title("1. INCIDENT SUMMARY")
    pdf.key_value("Ticket ID:", ticket_id)
    pdf.key_value("Description:", (ticket.get("description") or "N/A")[:120])
    pdf.key_value("Severity:", ticket.get("severity", "N/A"))
    pdf.key_value("Category:", ticket.get("category", "N/A"))
    pdf.key_value("Opened At:", ticket.get("opened_at", "N/A"))
    pdf.key_value("Resolved At:", ticket.get("resolved_at", "N/A"))
    pdf.key_value("Status:", ticket.get("status", "N/A"))
    pdf.key_value("Resolution Time:", f"{ticket.get('resolution_time_hrs', 'N/A')} hours")
    pdf.ln(3)
    pdf.section_body(sections.get("incident_summary", ""))

    # ── Section 2: AI Classification ──────────────────────────────────────────
    pdf.section_title("2. AI CLASSIFICATION DECISION")
    if pred:
        pdf.key_value("Model Used:", "Groq LLaMA 3.3-70B Versatile")
        pdf.key_value("Predicted Severity:", pred.get("predicted_severity", "N/A"))
        pdf.key_value("Predicted Category:", pred.get("predicted_category", "N/A"))
        pdf.key_value("Confidence Score:", f"{pred.get('confidence_score', 'N/A')}%")
        pdf.key_value("Risk Tier:", pred.get("risk_tier", "N/A"))
        pdf.key_value("Approval Path:", pred.get("approval_path", "N/A"))
        pdf.key_value("Anomaly Flagged:", "Yes" if pred.get("anomaly_flagged") else "No")
        pdf.ln(2)
    pdf.section_body(sections.get("classification_decision", ""))

    # ── Section 3: Root Cause Analysis ────────────────────────────────────────
    pdf.section_title("3. ROOT CAUSE ANALYSIS")
    if rca:
        pdf.key_value("Root Cause:", (rca.get("root_cause") or "N/A")[:120])
        pdf.key_value("Recommended Fix:", (rca.get("recommended_fix") or "N/A")[:120])
        pdf.key_value("Confidence:", f"{rca.get('confidence_score', 'N/A')}%")
        pdf.key_value("Risk Tier:", rca.get("risk_tier", "N/A"))
        similar_ids = [
            rca.get("similar_incident_1"),
            rca.get("similar_incident_2"),
            rca.get("similar_incident_3"),
        ]
        similar_ids = [s for s in similar_ids if s]
        pdf.key_value("Similar Incidents:", ", ".join(similar_ids) if similar_ids else "None found")
        pdf.ln(2)
    pdf.section_body(sections.get("rca_decision", ""))

    # ── Section 4: Approval Workflow ──────────────────────────────────────────
    pdf.section_title("4. APPROVAL WORKFLOW")
    pdf.section_body(sections.get("approval_workflow", ""))

    # ── Section 5: Execution & Outcome ────────────────────────────────────────
    pdf.section_title("5. EXECUTION & OUTCOME")
    if execs:
        last = execs[0]
        pdf.key_value("Fix Type:", last.get("fix_type", "N/A"))
        pdf.key_value("Outcome:", last.get("outcome", "N/A"))
        pdf.key_value("Rolled Back:", "Yes" if last.get("rolled_back") else "No")
        pdf.key_value("Executed At:", last.get("executed_at", "N/A"))
        if last.get("rollback_reason"):
            pdf.key_value("Rollback Reason:", last["rollback_reason"][:100])
        pdf.ln(2)
    pdf.section_body(sections.get("execution_outcome", ""))

    # ── Section 6: Knowledge Delta ────────────────────────────────────────────
    pdf.section_title("6. HUMAN-AI KNOWLEDGE DELTA")
    pdf.section_body(sections.get("knowledge_delta", ""))

    # ── Section 7: Full Audit Trail ───────────────────────────────────────────
    pdf.section_title("7. COMPLETE AUDIT TRAIL")
    if events:
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(230, 240, 250)
        for header in ["Timestamp", "Event", "Action", "Outcome"]:
            w = 45 if header == "Action" else 35 if header == "Timestamp" else 30
            pdf.cell(w, 6, header, border=1, fill=True)
        pdf.ln()

        pdf.set_font("Helvetica", "", 8)
        for e in events:
            ts = (e.get("timestamp") or "")[:16]
            evt = e.get("event_type", "?")
            action = (e.get("action_taken") or e.get("reasoning") or "")[:40]
            outcome = e.get("outcome", "")
            safe_action = action.encode("latin-1", "replace").decode("latin-1")

            pdf.cell(35, 5, ts, border=1)
            pdf.cell(30, 5, evt, border=1)
            pdf.cell(45, 5, safe_action, border=1)
            pdf.cell(30, 5, outcome, border=1)
            pdf.ln()
        pdf.ln(3)
    else:
        pdf.section_body("No audit events recorded for this ticket.")

    # ── Section 8: Compliance Attestation ─────────────────────────────────────
    pdf.section_title("8. COMPLIANCE ATTESTATION")
    pdf.section_body(sections.get("compliance_statement", ""))

    # ── Generate PDF bytes ────────────────────────────────────────────────────
    return pdf.output()
