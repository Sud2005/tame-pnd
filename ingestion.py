"""
Phase 1 — FastAPI Ingestion Pipeline
======================================
Handles ticket ingestion, normalization, and storage.
This is the entry point for all tickets — live or demo-fed.

Run with:
    uvicorn api.ingestion:app --reload --port 8000
"""

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DB_PATH = "db/opsai.db"

app = FastAPI(
    title="OpsAI Ingestion API",
    description="Phase 1 — Ticket ingestion and storage for Human-Governed AIOps",
    version="1.0.0",
)

# Allow React frontend to call this
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── DB helper ───────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def write_audit(conn, event_type, ticket_id, **kwargs):
    conn.execute("""
        INSERT INTO audit_log (id, event_type, ticket_id, action_taken, timestamp,
                               operator_id, confidence, risk_tier, reasoning, outcome)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(uuid.uuid4()),
        event_type,
        ticket_id,
        kwargs.get("action_taken", ""),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        kwargs.get("operator_id", "system"),
        kwargs.get("confidence"),
        kwargs.get("risk_tier"),
        kwargs.get("reasoning", ""),
        kwargs.get("outcome", ""),
    ))


# ─── Request / Response Models ────────────────────────────────────────────────

class TicketIngest(BaseModel):
    description:      str  = Field(..., min_length=5, description="Ticket description")
    severity:         Optional[str] = Field(None,  description="P1/P2/P3 — auto-detected if omitted")
    category:         Optional[str] = Field(None,  description="Auto-detected if omitted")
    assigned_group:   Optional[str] = Field(None)
    source:           Optional[str] = Field("manual", description="manual / csv_feed / monitoring")


class TicketResponse(BaseModel):
    id:               str
    description:      str
    severity:         str
    category:         str
    status:           str
    opened_at:        str
    anomaly_flags:    list[str]
    message:          str


# ─── Keyword-based anomaly detection (deterministic, no LLM needed) ──────────

ANOMALY_RULES = {
    "P1_KEYWORDS": [
        "production down", "all users", "data loss", "complete outage",
        "critical failure", "unresponsive", "breach", "corrupted",
        "all services", "entire system", "cannot login", "site down",
    ],
    "P2_KEYWORDS": [
        "degraded", "slow performance", "intermittent", "partial outage",
        "some users", "timeout", "high cpu", "memory leak", "disk full",
        "connection refused", "authentication failing",
    ],
    "SECURITY_KEYWORDS": [
        "unauthorized", "breach", "injection", "exploit", "malware",
        "ransomware", "suspicious", "intrusion",
    ],
}


def run_keyword_analysis(description: str) -> dict:
    desc_lower = description.lower()
    flags = []
    suggested_severity = None

    for keyword in ANOMALY_RULES["P1_KEYWORDS"]:
        if keyword in desc_lower:
            flags.append(f"P1_SIGNAL: '{keyword}'")
            suggested_severity = "P1"
            break

    if not suggested_severity:
        for keyword in ANOMALY_RULES["P2_KEYWORDS"]:
            if keyword in desc_lower:
                flags.append(f"P2_SIGNAL: '{keyword}'")
                suggested_severity = "P2"
                break

    for keyword in ANOMALY_RULES["SECURITY_KEYWORDS"]:
        if keyword in desc_lower:
            flags.append(f"SECURITY_FLAG: '{keyword}'")
            # Security always escalates to at least P2
            if suggested_severity not in ("P1",):
                suggested_severity = "P1"

    return {
        "flags": flags,
        "suggested_severity": suggested_severity,
        "anomaly_detected": len(flags) > 0,
    }


def detect_category(description: str) -> str:
    desc_lower = description.lower()
    category_keywords = {
        "Database":       ["database", "db", "sql", "query", "connection pool", "replica",
                           "deadlock", "ora-", "mysql", "postgres", "mongodb"],
        "Network":        ["network", "dns", "firewall", "vpn", "packet loss", "latency",
                           "bandwidth", "switch", "router", "vlan", "ssl certificate"],
        "Authentication": ["login", "auth", "sso", "ldap", "active directory", "password",
                           "token", "mfa", "oauth", "saml", "session"],
        "Infrastructure": ["server", "cpu", "memory", "disk", "storage", "container",
                           "kubernetes", "vm", "hypervisor", "terraform", "infra"],
        "Application":    ["api", "application", "app", "deployment", "release", "bug",
                           "crash", "error 500", "timeout", "import", "export"],
    }
    scores = {}
    for category, keywords in category_keywords.items():
        scores[category] = sum(1 for kw in keywords if kw in desc_lower)

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "General"


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "OpsAI Ingestion", "timestamp": datetime.now().isoformat()}


@app.post("/tickets/ingest", response_model=TicketResponse)
def ingest_ticket(ticket: TicketIngest):
    """
    Main ingestion endpoint.
    Accepts a ticket, runs keyword analysis, stores it, writes audit log.
    Phase 2 (prediction engine) will be called from here later.
    """
    conn = get_db()
    ticket_id = f"INC{uuid.uuid4().hex[:8].upper()}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Step 1: Keyword anomaly detection
    analysis = run_keyword_analysis(ticket.description)

    # Step 2: Determine severity
    # Priority: explicit > keyword-detected > default P3
    severity = (
        ticket.severity.upper()                    if ticket.severity
        else analysis["suggested_severity"]         if analysis["suggested_severity"]
        else "P3"
    )
    if severity not in ("P1", "P2", "P3"):
        severity = "P3"

    # Step 3: Determine category
    category = ticket.category or detect_category(ticket.description)

    # Step 4: Store ticket
    conn.execute("""
        INSERT INTO tickets 
        (id, description, severity, category, opened_at, status, assigned_group)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (ticket_id, ticket.description, severity, category,
          now, "open", ticket.assigned_group or "UNASSIGNED"))

    # Step 5: Write audit log
    write_audit(conn, "INGEST", ticket_id,
                action_taken=f"Ticket ingested via {ticket.source}",
                reasoning=f"Keyword flags: {analysis['flags']}",
                outcome="created")

    conn.commit()
    conn.close()

    return TicketResponse(
        id=ticket_id,
        description=ticket.description,
        severity=severity,
        category=category,
        status="open",
        opened_at=now,
        anomaly_flags=analysis["flags"],
        message=f"Ticket {ticket_id} ingested. Severity: {severity}. "
                f"{'⚠️ Anomaly detected!' if analysis['anomaly_detected'] else 'No anomalies flagged.'}",
    )


@app.get("/tickets")
def list_tickets(
    status:   Optional[str] = None,
    severity: Optional[str] = None,
    limit:    int = 50,
    offset:   int = 0,
):
    """List tickets with optional filters."""
    conn = get_db()
    query  = "SELECT * FROM tickets WHERE 1=1"
    params = []

    if status:
        query += " AND status = ?"
        params.append(status)
    if severity:
        query += " AND severity = ?"
        params.append(severity.upper())

    query += " ORDER BY opened_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    rows = conn.execute(query, params).fetchall()
    total = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE 1=1"
        + (" AND status=?" if status else "")
        + (" AND severity=?" if severity else ""),
        [p for p in params if p not in (limit, offset)]
    ).fetchone()[0]

    conn.close()
    return {
        "tickets": [dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    """Get a single ticket by ID."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found")
    return dict(row)


@app.get("/tickets/{ticket_id}/audit")
def get_ticket_audit(ticket_id: str):
    """Get full audit trail for a specific ticket."""
    conn = get_db()
    # Verify ticket exists
    ticket = conn.execute(
        "SELECT id FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if not ticket:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found")

    rows = conn.execute(
        "SELECT * FROM audit_log WHERE ticket_id = ? ORDER BY timestamp ASC",
        (ticket_id,)
    ).fetchall()
    conn.close()
    return {"ticket_id": ticket_id, "events": [dict(r) for r in rows]}


@app.get("/audit")
def list_audit(limit: int = 100, event_type: Optional[str] = None):
    """Full system audit log — exportable."""
    conn = get_db()
    query  = "SELECT * FROM audit_log WHERE 1=1"
    params = []

    if event_type:
        query += " AND event_type = ?"
        params.append(event_type.upper())

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return {"events": [dict(r) for r in rows], "total": len(rows)}


@app.get("/stats")
def get_stats():
    """Dashboard summary statistics."""
    conn = get_db()
    stats = {
        "total_tickets":   conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0],
        "open_tickets":    conn.execute("SELECT COUNT(*) FROM tickets WHERE status='open'").fetchone()[0],
        "resolved_today":  conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE status='resolved' AND DATE(opened_at)=DATE('now')"
        ).fetchone()[0],
        "p1_open":         conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE severity='P1' AND status='open'"
        ).fetchone()[0],
        "p2_open":         conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE severity='P2' AND status!='resolved'"
        ).fetchone()[0],
        "audit_events":    conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
        "auto_executed":   conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE event_type='EXECUTE' AND operator_id='system'"
        ).fetchone()[0],
    }
    conn.close()
    return stats


@app.post("/tickets/bulk-ingest")
def bulk_ingest(tickets: list[TicketIngest]):
    """Ingest multiple tickets at once — used for CSV demo feed."""
    results = []
    for t in tickets[:50]:  # cap at 50 per call
        result = ingest_ticket(t)
        results.append(result)
    return {"ingested": len(results), "tickets": results}
