"""
Phase 1+2+3 — FastAPI Ingestion Pipeline (Fixed)
==================================================
Fixes in this version:
  1. Severity NEVER overridden when explicitly provided
  2. Keyword engine only fires when NO severity given — uses exact phrases only
  3. GET /tickets/{id}/rca/result now returns full similar_incidents array
  4. POST /tickets/{id}/reject — keeps ticket open, logs rejection
  5. POST /tickets/{id}/rollback — reverts status, penalises confidence
  6. exclude_resolved + ORDER BY created_at for live feed

Run: uvicorn ingestion:app --reload --port 8000
"""

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DB_PATH = "db/opsai.db"

try:
    from prediction import predict_ticket
    PREDICTION_ENABLED = True
    print("✅ Phase 2: Prediction engine loaded")
except ImportError:
    PREDICTION_ENABLED = False
    print("⚠️  Phase 2: prediction.py not found")

try:
    from rca_engine import run_rca, add_to_index, build_index, prewarm_index
    RCA_ENABLED = True
    print("✅ Phase 3: RCA engine loaded")
except ImportError:
    RCA_ENABLED = False
    print("⚠️  Phase 3: rca_engine.py not found")

app = FastAPI(title="OpsAI API", version="4.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def startup_event():
    if RCA_ENABLED:
        # Non-blocking prewarm: loads model + FAISS index in background thread
        # API is immediately responsive while index loads (~10-20s first time)
        prewarm_index()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def write_audit(conn, event_type, ticket_id, **kwargs):
    conn.execute("""
        INSERT INTO audit_log
        (id, event_type, ticket_id, action_taken, timestamp,
         operator_id, confidence, risk_tier, reasoning, outcome)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        str(uuid.uuid4()), event_type, ticket_id,
        kwargs.get("action_taken", ""),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        kwargs.get("operator_id", "system"),
        kwargs.get("confidence"),
        kwargs.get("risk_tier"),
        kwargs.get("reasoning", ""),
        kwargs.get("outcome", ""),
    ))


class TicketIngest(BaseModel):
    description:    str  = Field(..., min_length=3)
    severity:       Optional[str] = None
    category:       Optional[str] = None
    ci_cat:         Optional[str] = None
    ci_subcat:      Optional[str] = None
    urgency:        Optional[str] = None
    impact:         Optional[str] = None
    alert_status:   Optional[str] = None
    assigned_group: Optional[str] = None
    source:         Optional[str] = "manual"


class TicketResponse(BaseModel):
    id: str; description: str; severity: str; category: str
    status: str; opened_at: str; anomaly_flags: list[str]; message: str


class ResolveRequest(BaseModel):
    resolution_notes: str = Field(..., min_length=5)
    resolved_by:      Optional[str] = "operator"


class RejectRequest(BaseModel):
    reason:      str = Field(..., min_length=3)
    rejected_by: Optional[str] = "operator"


class RollbackRequest(BaseModel):
    reason:         str = Field(..., min_length=3)
    rolled_back_by: Optional[str] = "operator"


# ── Keyword engine — EXACT PHRASES ONLY ──────────────────────────────────────
# Only runs when caller provides NO explicit severity.
# Uses multi-word phrases to avoid false positives on words like "slow", "timeout".

P1_PHRASES = [
    "production down", "all users locked out", "complete outage",
    "entire system down", "site down", "data loss confirmed",
    "all services unreachable", "cluster failure", "all users affected",
]
P2_PHRASES = [
    "partial outage", "subset of users affected", "service degraded",
    "elevated error rate", "replication lag critical", "memory pressure critical",
]
SECURITY_PHRASES = [
    "unauthorized access detected", "data breach confirmed",
    "ransomware detected", "malware detected", "active exploit",
]


def run_keyword_analysis(description: str, explicit_severity: str = None) -> dict:
    # NEVER override explicit severity
    if explicit_severity:
        return {"flags": [], "suggested_severity": explicit_severity, "anomaly_detected": False}

    desc  = description.lower()
    flags = []
    suggested = None

    for phrase in P1_PHRASES:
        if phrase in desc:
            flags.append(f"P1: '{phrase}'")
            suggested = "P1"
            break

    if not suggested:
        for phrase in P2_PHRASES:
            if phrase in desc:
                flags.append(f"P2: '{phrase}'")
                suggested = "P2"
                break

    for phrase in SECURITY_PHRASES:
        if phrase in desc:
            flags.append(f"SECURITY: '{phrase}'")
            suggested = "P1"
            break

    return {"flags": flags, "suggested_severity": suggested, "anomaly_detected": bool(flags)}


def detect_category(description: str, ci_cat: str = "") -> str:
    ci_map = {
        "storage": "Database", "database": "Database",
        "network": "Network", "networkcomponents": "Network",
        "hardware": "Infrastructure",
        "application": "Application", "subapplication": "Application",
    }
    if ci_cat and ci_cat.lower() in ci_map:
        return ci_map[ci_cat.lower()]
    desc = description.lower()
    kws  = {
        "Database":       ["database", "db ", "sql", "storage", "oracle"],
        "Network":        ["network", "dns", "firewall", "vpn", "switch"],
        "Authentication": ["login", "auth", "sso", "password", "token"],
        "Infrastructure": ["server", "cpu", "memory", "disk", "container"],
        "Application":    ["application", "api", "web based", "deployment"],
    }
    scores = {c: sum(1 for k in ws if k in desc) for c, ws in kws.items()}
    best   = max(scores, key=scores.get)
    return best if scores[best] > 0 else "General"


def normalize_severity(raw: str) -> str:
    m = {
        "1": "P1", "2": "P2", "3": "P3", "4": "P3", "5": "P3",
        "1.0": "P1", "2.0": "P2", "3.0": "P3", "4.0": "P3",
        "p1": "P1", "p2": "P2", "p3": "P3",
        "critical": "P1", "high": "P2", "medium": "P3", "low": "P3",
    }
    return m.get(str(raw).lower().strip(), "P3")


def bg_predict(ticket_id, description, category, ci_cat, ci_subcat, urgency, impact, alert_status):
    if not PREDICTION_ENABLED:
        return
    try:
        predict_ticket(
            ticket_id=ticket_id, description=description, category_hint=category,
            ci_cat=ci_cat, ci_subcat=ci_subcat, category=category,
            urgency=urgency, impact=impact, alert_status=alert_status,
        )
    except Exception as e:
        print(f"⚠️  bg_predict ({ticket_id}): {e}")


def bg_rca(ticket_id):
    if not RCA_ENABLED:
        return
    try:
        run_rca(ticket_id)
    except Exception as e:
        print(f"⚠️  bg_rca ({ticket_id}): {e}")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "phase": "1+2+3" if (PREDICTION_ENABLED and RCA_ENABLED) else "1+2" if PREDICTION_ENABLED else "1",
        "prediction_engine": PREDICTION_ENABLED, "rca_engine": RCA_ENABLED,
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/tickets/ingest", response_model=TicketResponse)
def ingest_ticket(ticket: TicketIngest, bg: BackgroundTasks):
    conn = get_db()
    tid  = f"INC{uuid.uuid4().hex[:8].upper()}"
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Resolve explicit severity first
    explicit_sev = normalize_severity(ticket.severity) if ticket.severity else None

    # Keyword analysis — skips override if explicit_sev is set
    kw = run_keyword_analysis(ticket.description, explicit_sev)

    # Final severity — explicit always wins
    severity = explicit_sev or kw["suggested_severity"] or "P3"
    if severity not in ("P1", "P2", "P3"):
        severity = "P3"

    category = ticket.category or detect_category(ticket.description, ticket.ci_cat or "")

    conn.execute("""
        INSERT INTO tickets (id, description, severity, category, opened_at, status, assigned_group)
        VALUES (?,?,?,?,?,?,?)
    """, (tid, ticket.description, severity, category, now, "open", ticket.assigned_group or "UNASSIGNED"))

    write_audit(conn, "INGEST", tid,
                action_taken=f"via {ticket.source}",
                reasoning=f"explicit={explicit_sev} flags={kw['flags']}",
                outcome="created")
    conn.commit()
    conn.close()

    bg.add_task(bg_predict, tid, ticket.description, category,
                ticket.ci_cat or "", ticket.ci_subcat or "",
                ticket.urgency or "", ticket.impact or "", ticket.alert_status or "")
    if severity in ("P1", "P2"):
        bg.add_task(bg_rca, tid)

    return TicketResponse(
        id=tid, description=ticket.description, severity=severity, category=category,
        status="open", opened_at=now, anomaly_flags=kw["flags"],
        message=f"{tid} | {severity} | {category} | RCA {'auto-triggered' if severity in ('P1','P2') else 'on-demand'}",
    )


@app.post("/tickets/{ticket_id}/rca")
def trigger_rca(ticket_id: str, bg: BackgroundTasks):
    if not RCA_ENABLED:
        raise HTTPException(503, "RCA engine not loaded")
    conn = get_db()
    if not conn.execute("SELECT id FROM tickets WHERE id=?", (ticket_id,)).fetchone():
        conn.close(); raise HTTPException(404, f"Ticket {ticket_id} not found")
    conn.close()
    bg.add_task(bg_rca, ticket_id)
    return {"message": f"RCA triggered for {ticket_id}", "result_url": f"/tickets/{ticket_id}/rca/result"}


@app.get("/tickets/{ticket_id}/rca/result")
def get_rca_result(ticket_id: str):
    """Returns full RCA with reconstructed similar_incidents array (not just IDs)."""
    conn = get_db()
    row  = conn.execute(
        "SELECT * FROM rca_results WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1",
        (ticket_id,)
    ).fetchone()

    if not row:
        conn.close()
        return {"ticket_id": ticket_id, "status": "pending", "message": "RCA not ready. Retry in 4s."}

    result = dict(row)
    try:
        scores = json.loads(result.get("similarity_scores", "[]"))
    except Exception:
        scores = []

    # Reconstruct full similar_incidents from stored IDs
    similar_incidents = []
    for i, id_field in enumerate(["similar_incident_1", "similar_incident_2", "similar_incident_3"]):
        sid = result.get(id_field)
        if not sid:
            continue
        t = conn.execute("SELECT * FROM tickets WHERE id=?", (sid,)).fetchone()
        if t:
            td = dict(t)
            similar_incidents.append({
                "id":             td["id"],
                "description":    (td.get("description") or "")[:120],
                "resolution":     td.get("resolution_notes") or "",
                "severity":       td.get("severity", ""),
                "category":       td.get("category", ""),
                "mttr_hrs":       td.get("resolution_time_hrs", ""),
                "similarity_score": scores[i] if i < len(scores) else 0,
                "similarity_pct":   round(scores[i] * 100, 1) if i < len(scores) else 0,
            })

    # Derive approval_path from stored data (column not in DB schema)
    ticket_row = conn.execute("SELECT severity FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    sev = dict(ticket_row).get("severity", "P3") if ticket_row else "P3"
    conf = int(result.get("confidence_score", 0) or 0)
    risk = str(result.get("risk_tier", "Medium"))
    if sev == "P1" or risk == "Critical" or (conf is not None and conf < 40):
        approval_path = "C"
    elif sev == "P3" and conf is not None and conf >= 75 and risk == "Low":
        approval_path = "A"
    elif conf is not None and conf >= 55:
        approval_path = "B"
    else:
        approval_path = "C"

    conn.close()
    result["similar_incidents"] = similar_incidents
    result["similarity_scores"] = scores
    result["approval_path"]     = approval_path
    result["status"]            = result.get("status") or "success"

    # Ensure fix_steps and pattern_match exist for frontend
    if "fix_steps" not in result or not result["fix_steps"]:
        fix = result.get("recommended_fix", "")
        result["fix_steps"] = [fix] if fix else ["Review logs", "Identify root cause", "Apply fix"]
    if isinstance(result.get("fix_steps"), str):
        try:
            result["fix_steps"] = json.loads(result["fix_steps"])
        except Exception:
            result["fix_steps"] = [result["fix_steps"]]
    if "pattern_match" not in result:
        result["pattern_match"] = ""

    return result


@app.post("/tickets/{ticket_id}/resolve")
def resolve_ticket(ticket_id: str, body: ResolveRequest):
    conn = get_db()
    row  = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row: conn.close(); raise HTTPException(404, f"Ticket {ticket_id} not found")
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE tickets SET status='resolved',resolved_at=?,resolution_notes=?,resolved_by=? WHERE id=?",
                 (now, body.resolution_notes, body.resolved_by, ticket_id))
    write_audit(conn, "RESOLVE", ticket_id, operator_id=body.resolved_by,
                action_taken="Approved and executed", reasoning=body.resolution_notes[:100], outcome="resolved")
    conn.commit()
    if RCA_ENABLED:
        ticket_dict = dict(row)
        ticket_dict["resolution_notes"] = body.resolution_notes
        ticket_dict["status"] = "resolved"
        try: add_to_index(ticket_dict)
        except Exception as e: print(f"⚠️  FAISS add: {e}")
    conn.close()
    return {"message": f"{ticket_id} resolved", "outcome": "success", "memory_updated": RCA_ENABLED}


@app.post("/tickets/{ticket_id}/reject")
def reject_ticket(ticket_id: str, body: RejectRequest):
    """Reject recommended fix. Ticket stays OPEN. No memory update."""
    conn = get_db()
    if not conn.execute("SELECT id FROM tickets WHERE id=?", (ticket_id,)).fetchone():
        conn.close(); raise HTTPException(404, f"Ticket {ticket_id} not found")
    conn.execute("UPDATE tickets SET status='open' WHERE id=?", (ticket_id,))
    write_audit(conn, "REJECT", ticket_id, operator_id=body.rejected_by,
                action_taken="Fix rejected by operator", reasoning=body.reason[:200], outcome="rejected")
    conn.commit(); conn.close()
    return {"message": f"Fix rejected. Ticket {ticket_id} remains open.", "outcome": "rejected"}


@app.post("/tickets/{ticket_id}/rollback")
def rollback_ticket(ticket_id: str, body: RollbackRequest):
    """Rollback executed fix. Reverts ticket to open. Penalises fix confidence."""
    conn = get_db()
    row  = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row: conn.close(); raise HTTPException(404, f"Ticket {ticket_id} not found")
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE tickets SET status='open',resolution_notes=NULL,resolved_at=NULL,resolved_by=NULL WHERE id=?",
                 (ticket_id,))
    write_audit(conn, "ROLLBACK", ticket_id, operator_id=body.rolled_back_by,
                action_taken="Fix rolled back", reasoning=body.reason[:200], outcome="rolled_back")
    category = dict(row).get("category", "General")
    conn.execute("""
        UPDATE fix_outcomes SET rollback_count=rollback_count+1, total_actions=total_actions+1, last_updated=?
        WHERE category=?
    """, (now, category))
    conn.commit(); conn.close()
    return {"message": f"Ticket {ticket_id} rolled back to open.", "outcome": "rolled_back"}


@app.get("/tickets/search")
def search_tickets(
    q:        str  = "",
    severity: Optional[str] = None,
    category: Optional[str] = None,
    status:   Optional[str] = None,
    limit:    int  = 100,
    offset:   int  = 0,
):
    """
    Full-text search across all 46,000 tickets.
    Used by the Memory Browser screen to explore the full dataset.
    No date filter — returns historical data too.
    """
    conn   = get_db()
    query  = "SELECT * FROM tickets WHERE 1=1"
    params = []

    if q:
        query += " AND (description LIKE ? OR resolution_notes LIKE ? OR id LIKE ?)"
        like = f"%{q}%"
        params += [like, like, like]
    if severity:
        query += " AND severity = ?"
        params.append(severity.upper())
    if category:
        query += " AND category = ?"
        params.append(category)
    if status:
        query += " AND status = ?"
        params.append(status)

    # Count query (same filters, no limit)
    count_query  = query.replace("SELECT *", "SELECT COUNT(*)")
    total        = conn.execute(count_query, params).fetchone()[0]

    query += " ORDER BY opened_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return {
        "tickets": [dict(r) for r in rows],
        "total":   total,
        "limit":   limit,
        "offset":  offset,
        "query":   q,
    }


@app.get("/tickets/overview")
def tickets_overview():
    """
    Aggregate stats for the Memory Browser.
    Shows breakdown of all 46,000 tickets by severity, category, status.
    """
    conn = get_db()
    by_severity = conn.execute("""
        SELECT severity, COUNT(*) as count FROM tickets GROUP BY severity ORDER BY severity
    """).fetchall()
    by_category = conn.execute("""
        SELECT category, COUNT(*) as count FROM tickets GROUP BY category ORDER BY count DESC
    """).fetchall()
    by_status = conn.execute("""
        SELECT status, COUNT(*) as count FROM tickets GROUP BY status ORDER BY count DESC
    """).fetchall()
    with_resolution = conn.execute("""
        SELECT COUNT(*) FROM tickets
        WHERE resolution_notes IS NOT NULL AND resolution_notes != ''
        AND resolution_notes NOT IN ('nan','None','NaN')
    """).fetchone()[0]
    avg_mttr = conn.execute("""
        SELECT ROUND(AVG(resolution_time_hrs), 2) FROM tickets
        WHERE resolution_time_hrs IS NOT NULL AND resolution_time_hrs > 0
    """).fetchone()[0]
    conn.close()
    return {
        "by_severity":    [dict(r) for r in by_severity],
        "by_category":    [dict(r) for r in by_category],
        "by_status":      [dict(r) for r in by_status],
        "with_resolution": with_resolution,
        "avg_mttr_hrs":   avg_mttr,
    }

@app.get("/tickets/{ticket_id}/prediction")
def get_prediction(ticket_id: str):
    conn = get_db()
    row  = conn.execute("SELECT * FROM predictions WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1",
                        (ticket_id,)).fetchone()
    conn.close()
    if not row: return {"ticket_id": ticket_id, "status": "pending", "message": "Retry in 3s."}
    return dict(row)


@app.get("/tickets")
def list_tickets(status: Optional[str]=None, severity: Optional[str]=None,
                 exclude_resolved: bool=False, limit: int=50, offset: int=0):
    conn = get_db(); q = "SELECT * FROM tickets WHERE 1=1"; params = []
    if exclude_resolved: q += " AND status != 'resolved'"
    # Always exclude historical ITSM dataset tickets from the live feed
    # These have created_at from when setup_db.py ran, not from live ingestion
    # Only show tickets ingested in the last 30 days via the API
    q += " AND opened_at LIKE '20%' AND opened_at >= datetime('now', '-30 days')"
    if status: q += " AND status=?"; params.append(status)
    if severity: q += " AND severity=?"; params.append(severity.upper())
    q += " ORDER BY opened_at DESC LIMIT ? OFFSET ?"; params += [limit, offset]
    rows  = conn.execute(q, params).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    conn.close()
    return {"tickets": [dict(r) for r in rows], "total": total}


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    conn = get_db(); row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404, f"Ticket {ticket_id} not found")
    return dict(row)


@app.get("/tickets/{ticket_id}/audit")
def get_ticket_audit(ticket_id: str):
    conn = get_db()
    rows = conn.execute("SELECT * FROM audit_log WHERE ticket_id=? ORDER BY timestamp ASC", (ticket_id,)).fetchall()
    conn.close()
    return {"ticket_id": ticket_id, "events": [dict(r) for r in rows]}


@app.get("/audit")
def list_audit(limit: int=200, event_type: Optional[str]=None):
    conn = get_db(); q = "SELECT * FROM audit_log WHERE 1=1"; params = []
    if event_type: q += " AND event_type=?"; params.append(event_type.upper())
    q += " ORDER BY timestamp DESC LIMIT ?"; params.append(limit)
    rows = conn.execute(q, params).fetchall(); conn.close()
    return {"events": [dict(r) for r in rows], "total": len(rows)}


@app.get("/stats")
def get_stats():
    conn = get_db()
    s = {
        "total_tickets":    conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0],
        # Only count tickets ingested via the API (created_at is recent)
        # Historical ITSM dataset tickets have opened_at in 2012-2014 but created_at from setup
        # We count "open" as tickets that are genuinely actionable (not the 46k historical ones)
        "open_tickets":     conn.execute("""
            SELECT COUNT(*) FROM tickets
            WHERE status = 'open'
            AND opened_at LIKE '20%' AND opened_at >= datetime('now', '-30 days')
        """).fetchone()[0],
        "pending_approval": conn.execute("SELECT COUNT(*) FROM tickets WHERE status='pending_approval'").fetchone()[0],
        "resolved":         conn.execute("""
            SELECT COUNT(*) FROM tickets
            WHERE status = 'resolved'
            AND opened_at LIKE '20%' AND opened_at >= datetime('now', '-30 days')
        """).fetchone()[0],
        "p1_open":          conn.execute("""
            SELECT COUNT(*) FROM tickets
            WHERE severity='P1' AND status!='resolved'
            AND opened_at LIKE '20%' AND opened_at >= datetime('now', '-30 days')
        """).fetchone()[0],
        "p2_open":          conn.execute("""
            SELECT COUNT(*) FROM tickets
            WHERE severity='P2' AND status!='resolved'
            AND opened_at LIKE '20%' AND opened_at >= datetime('now', '-30 days')
        """).fetchone()[0],
        "predictions_run":  conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0],
        "rca_completed":    conn.execute("SELECT COUNT(*) FROM rca_results").fetchone()[0],
        "audit_events":     conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
        "phase":            "1+2+3" if (PREDICTION_ENABLED and RCA_ENABLED) else "1+2" if PREDICTION_ENABLED else "1",
    }
    conn.close(); return s
