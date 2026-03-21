"""
Phase 1+2 — FastAPI Ingestion Pipeline (Updated)
==================================================
Now wires Phase 2 prediction engine into every ticket ingestion.
Handles actual ITSM_data.csv column names as optional fields.

Run with:
    uvicorn ingestion:app --reload --port 8000
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

# Phase 2 prediction — imported here, fails gracefully if Groq key not set yet
try:
    from prediction import predict_ticket
    PREDICTION_ENABLED = True
    print("✅ Phase 2 prediction engine loaded")
except ImportError:
    PREDICTION_ENABLED = False
    print("⚠️  prediction.py not found — running Phase 1 only")

app = FastAPI(
    title="OpsAI API",
    description="Human-Governed Autonomous AI Support — Phase 1 + 2",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── DB helper ─────────────────────────────────────────────────────────────────

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
        kwargs.get("action_taken",""),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        kwargs.get("operator_id","system"),
        kwargs.get("confidence"),
        kwargs.get("risk_tier"),
        kwargs.get("reasoning",""),
        kwargs.get("outcome",""),
    ))


# ── Models ────────────────────────────────────────────────────────────────────

class TicketIngest(BaseModel):
    # Core (required)
    description:    str  = Field(..., min_length=3)

    # ITSM dataset fields (all optional — auto-detected if omitted)
    severity:       Optional[str] = None   # P1/P2/P3 or 1/2/3/4
    category:       Optional[str] = None
    ci_cat:         Optional[str] = None   # CI_Cat column
    ci_subcat:      Optional[str] = None   # CI_Subcat column
    urgency:        Optional[str] = None   # Urgency column
    impact:         Optional[str] = None   # Impact column
    alert_status:   Optional[str] = None   # Alert_Status column
    assigned_group: Optional[str] = None
    source:         Optional[str] = "manual"


class TicketResponse(BaseModel):
    id:             str
    description:    str
    severity:       str
    category:       str
    status:         str
    opened_at:      str
    anomaly_flags:  list[str]
    prediction:     Optional[dict] = None   # Phase 2 result
    message:        str


# ── Keyword layer (Phase 1 — deterministic, always runs) ─────────────────────

P1_KW = ["production down","all users","data loss","complete outage","critical failure",
          "unresponsive","breach","corrupted","all services","entire system","site down"]
P2_KW = ["degraded","slow performance","intermittent","partial","some users","timeout",
          "high cpu","memory leak","disk full","connection refused"]
SEC_KW = ["unauthorized","breach","injection","exploit","malware","ransomware","suspicious"]

CI_CAT_RISK = {"storage":"P2","hardware":"P1","network":"P1","application":"P2","subapplication":"P2"}


def keyword_analyze(description: str, ci_cat: str = "", alert_status: str = "") -> dict:
    desc  = description.lower()
    flags, suggested = [], None

    # Alert_Status field from dataset
    if str(alert_status).lower() == "true":
        flags.append("ACTIVE_ALERT: Alert_Status=True")
        suggested = "P1"

    # CI_Cat field from dataset
    ci_risk = CI_CAT_RISK.get(str(ci_cat).lower())
    if ci_risk and not suggested:
        flags.append(f"CI_CAT_RISK: {ci_cat}")
        suggested = ci_risk

    for kw in P1_KW:
        if kw in desc:
            flags.append(f"P1_SIGNAL: '{kw}'")
            suggested = "P1"
            break

    if suggested != "P1":
        for kw in P2_KW:
            if kw in desc:
                flags.append(f"P2_SIGNAL: '{kw}'")
                suggested = suggested or "P2"
                break

    for kw in SEC_KW:
        if kw in desc:
            flags.append(f"SECURITY: '{kw}'")
            suggested = "P1"

    return {"flags": flags, "suggested_severity": suggested, "anomaly_detected": bool(flags)}


def detect_category(description: str, ci_cat: str = "", ci_subcat: str = "") -> str:
    # Use CI_Cat from dataset if available
    cat_map = {
        "storage": "Database", "database": "Database",
        "network": "Network",
        "hardware": "Infrastructure",
        "application": "Application",
        "subapplication": "Application",
    }
    if ci_cat and ci_cat.lower() in cat_map:
        return cat_map[ci_cat.lower()]

    desc = (description + " " + ci_subcat).lower()
    kws  = {
        "Database":       ["database","db","sql","query","storage","oracle","postgres","mysql"],
        "Network":        ["network","dns","firewall","vpn","packet","bandwidth","switch","vlan"],
        "Authentication": ["login","auth","sso","ldap","password","token","mfa","session"],
        "Infrastructure": ["server","cpu","memory","disk","container","kubernetes","vm","infra"],
        "Application":    ["application","api","app","deployment","crash","web based","desktop"],
    }
    scores = {cat: sum(1 for kw in ws if kw in desc) for cat, ws in kws.items()}
    best   = max(scores, key=scores.get)
    return best if scores[best] > 0 else "General"


def normalize_severity(raw: str) -> str:
    """Handles 1/2/3/4/5 from ITSM dataset and P1/P2/P3 from manual input."""
    m = {"1":"P1","2":"P2","3":"P3","4":"P3","5":"P3",
         "p1":"P1","p2":"P2","p3":"P3",
         "critical":"P1","high":"P2","medium":"P3","low":"P3"}
    return m.get(str(raw).lower().strip(), "P3")


# ── Background prediction (non-blocking) ─────────────────────────────────────

def run_prediction(ticket_id: str, description: str, category: str,
                   ci_cat: str, ci_subcat: str, urgency: str,
                   impact: str, alert_status: str):
    if not PREDICTION_ENABLED:
        return
    try:
        predict_ticket(
            ticket_id=ticket_id,
            description=description,
            category_hint=category,
            ci_cat=ci_cat,
            ci_subcat=ci_subcat,
            category=category,
            urgency=urgency,
            impact=impact,
            alert_status=alert_status,
        )
    except Exception as e:
        print(f"⚠️  Background prediction error ({ticket_id}): {e}")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "phase": "1+2" if PREDICTION_ENABLED else "1",
        "prediction_engine": PREDICTION_ENABLED,
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/tickets/ingest", response_model=TicketResponse)
def ingest_ticket(ticket: TicketIngest, bg: BackgroundTasks):
    conn     = get_db()
    tid      = f"INC{uuid.uuid4().hex[:8].upper()}"
    now      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Layer 1: keyword analysis
    kw = keyword_analyze(
        ticket.description,
        ticket.ci_cat or "",
        ticket.alert_status or "",
    )

    # Severity: explicit → keyword-detected → default P3
    if ticket.severity:
        severity = normalize_severity(ticket.severity)
    elif kw["suggested_severity"]:
        severity = kw["suggested_severity"]
    else:
        severity = "P3"

    category = ticket.category or detect_category(
        ticket.description, ticket.ci_cat or "", ticket.ci_subcat or ""
    )

    conn.execute("""
        INSERT INTO tickets
        (id, description, severity, category, opened_at, status, assigned_group)
        VALUES (?,?,?,?,?,?,?)
    """, (tid, ticket.description, severity, category,
          now, "open", ticket.assigned_group or "UNASSIGNED"))

    write_audit(conn, "INGEST", tid,
                action_taken=f"Ticket ingested via {ticket.source}",
                reasoning=f"KW flags: {kw['flags']}",
                outcome="created")
    conn.commit()
    conn.close()

    # Phase 2: Groq prediction runs in background (non-blocking)
    bg.add_task(
        run_prediction, tid, ticket.description, category,
        ticket.ci_cat or "", ticket.ci_subcat or "",
        ticket.urgency or "", ticket.impact or "",
        ticket.alert_status or "",
    )

    return TicketResponse(
        id=tid, description=ticket.description,
        severity=severity, category=category,
        status="open", opened_at=now,
        anomaly_flags=kw["flags"],
        prediction=None,   # Will be available via GET /tickets/{id}/prediction
        message=(
            f"Ticket {tid} ingested. Severity: {severity}. "
            f"{'⚠️ Anomaly!' if kw['anomaly_detected'] else 'No anomaly.'} "
            f"{'AI prediction running...' if PREDICTION_ENABLED else ''}"
        ),
    )


@app.get("/tickets/{ticket_id}/prediction")
def get_prediction(ticket_id: str):
    """Get the AI prediction result for a ticket (available ~2s after ingestion)."""
    conn = get_db()
    row  = conn.execute(
        "SELECT * FROM predictions WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1",
        (ticket_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {"ticket_id": ticket_id, "status": "pending",
                "message": "Prediction not yet available. Retry in 2-3 seconds."}
    return dict(row)


@app.get("/tickets")
def list_tickets(status: Optional[str]=None, severity: Optional[str]=None,
                 limit: int=50, offset: int=0):
    conn   = get_db()
    q      = "SELECT * FROM tickets WHERE 1=1"
    params = []
    if status:
        q += " AND status=?"; params.append(status)
    if severity:
        q += " AND severity=?"; params.append(severity.upper())
    q += " ORDER BY opened_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows  = conn.execute(q, params).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    conn.close()
    return {"tickets": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    conn = get_db()
    row  = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, f"Ticket {ticket_id} not found")
    return dict(row)


@app.get("/tickets/{ticket_id}/audit")
def get_ticket_audit(ticket_id: str):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE ticket_id=? ORDER BY timestamp ASC", (ticket_id,)
    ).fetchall()
    conn.close()
    return {"ticket_id": ticket_id, "events": [dict(r) for r in rows]}


@app.get("/audit")
def list_audit(limit: int=100, event_type: Optional[str]=None):
    conn   = get_db()
    q      = "SELECT * FROM audit_log WHERE 1=1"
    params = []
    if event_type:
        q += " AND event_type=?"; params.append(event_type.upper())
    q += " ORDER BY timestamp DESC LIMIT ?"; params.append(limit)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return {"events": [dict(r) for r in rows], "total": len(rows)}


@app.get("/stats")
def get_stats():
    conn = get_db()
    stats = {
        "total_tickets":  conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0],
        "open_tickets":   conn.execute("SELECT COUNT(*) FROM tickets WHERE status='open'").fetchone()[0],
        "p1_open":        conn.execute("SELECT COUNT(*) FROM tickets WHERE severity='P1' AND status='open'").fetchone()[0],
        "p2_open":        conn.execute("SELECT COUNT(*) FROM tickets WHERE severity='P2' AND status!='resolved'").fetchone()[0],
        "predictions_run":conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0],
        "audit_events":   conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
        "auto_executed":  conn.execute("SELECT COUNT(*) FROM audit_log WHERE event_type='EXECUTE' AND operator_id='system'").fetchone()[0],
        "phase":          "1+2" if PREDICTION_ENABLED else "1",
    }
    conn.close()
    return stats
