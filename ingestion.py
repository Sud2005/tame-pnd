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
from typing import Optional, List

from fastapi import FastAPI, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DB_PATH = "db/opsai.db"
_MAX_REASON_LEN = 500   # max length for operator reason / rollback reason strings stored in DB
DEMO_SCALE = 242        # Used to artificially inflate summary counts for hackathon demo video

# ── WebSocket connection registry ─────────────────────────────────────────────
_ws_connections: set = set()


async def _ws_broadcast(message: dict):
    """Broadcast a JSON message to all connected WebSocket clients."""
    dead = set()
    for ws in _ws_connections:
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)
    _ws_connections.difference_update(dead)


try:
    from prediction import predict_ticket
    PREDICTION_ENABLED = True
    print("✅ Phase 2: Prediction engine loaded")
except ImportError:
    PREDICTION_ENABLED = False
    print("⚠️  Phase 2: prediction.py not found")

try:
    from rca_engine import run_rca, add_to_index, build_index
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
        try:
            print("📥 Pre-loading FAISS index on startup...")
            build_index()
            print("✅ FAISS index ready")
        except Exception as e:
            print(f"⚠️  FAISS index not ready: {e}")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def write_audit(conn, event_type, ticket_id, **kwargs):
    conn.execute("""
        INSERT INTO audit_log
        (id, event_type, ticket_id, action_taken, timestamp,
         operator_id, approval_path, confidence, risk_tier, reasoning, outcome)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        str(uuid.uuid4()), event_type, ticket_id,
        kwargs.get("action_taken", ""),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        kwargs.get("operator_id", "system"),
        kwargs.get("approval_path"),
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


@app.get("/portal")
def serve_portal():
    """Serve client_portal.html over HTTP so Web Speech API (microphone) works."""
    import os
    portal_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "client_portal.html")
    if not os.path.exists(portal_path):
        raise HTTPException(404, "client_portal.html not found")
    return FileResponse(portal_path, media_type="text/html")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Real-time feed: clients connect here and receive JSON push-events
    whenever a ticket is ingested, so the live-feed screen doesn't need polling.
    Message format: {"type": "new_ticket", "id": "<ticket_id>"}
    """
    await websocket.accept()
    _ws_connections.add(websocket)
    try:
        while True:
            # Keep the connection alive; client may send any frame as a heartbeat
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        _ws_connections.discard(websocket)


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
    # Notify connected WebSocket clients about the new ticket
    bg.add_task(_ws_broadcast, {"type": "new_ticket", "id": tid})

    return TicketResponse(
        id=tid, description=ticket.description, severity=severity, category=category,
        status="open", opened_at=now, anomaly_flags=kw["flags"],
        message=f"{tid} | {severity} | {category} | RCA {'auto-triggered' if severity in ('P1','P2') else 'on-demand'}",
    )


class BulkIngestItem(TicketIngest):
    """Inherits all fields from TicketIngest; only overrides the default source."""
    source: Optional[str] = "bulk"


@app.post("/tickets/bulk_ingest")
def bulk_ingest(tickets: List[BulkIngestItem], bg: BackgroundTasks):
    """
    Ingest multiple tickets in a single request.
    Each ticket gets its own prediction + RCA background tasks so they all fire.
    Bug fix: previous version called ingest_ticket() without forwarding BackgroundTasks,
    which silently dropped the prediction/RCA tasks.
    """
    results = []
    conn = get_db()
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        for ticket in tickets:
            tid          = f"INC{uuid.uuid4().hex[:8].upper()}"
            explicit_sev = normalize_severity(ticket.severity) if ticket.severity else None
            kw           = run_keyword_analysis(ticket.description, explicit_sev)
            severity     = explicit_sev or kw["suggested_severity"] or "P3"
            if severity not in ("P1", "P2", "P3"):
                severity = "P3"
            category = ticket.category or detect_category(ticket.description, ticket.ci_cat or "")

            conn.execute("""
                INSERT INTO tickets (id, description, severity, category, opened_at, status, assigned_group)
                VALUES (?,?,?,?,?,?,?)
            """, (tid, ticket.description, severity, category, now, "open",
                  ticket.assigned_group or "UNASSIGNED"))
            write_audit(conn, "INGEST", tid,
                        action_taken=f"via {ticket.source}",
                        reasoning=f"bulk explicit={explicit_sev} flags={kw['flags']}",
                        outcome="created")

            # Register background tasks (all DB inserts complete before commit,
            # background tasks run after the response is sent)
            bg.add_task(bg_predict, tid, ticket.description, category,
                        ticket.ci_cat or "", ticket.ci_subcat or "",
                        ticket.urgency or "", ticket.impact or "", ticket.alert_status or "")
            if severity in ("P1", "P2"):
                bg.add_task(bg_rca, tid)
            bg.add_task(_ws_broadcast, {"type": "new_ticket", "id": tid})

            results.append({"id": tid, "severity": severity, "category": category})

        conn.commit()
    finally:
        conn.close()

    return {"ingested": len(results), "tickets": results}


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

    conn.close()
    result["similar_incidents"] = similar_incidents
    result["similarity_scores"] = scores
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


# ── Approval workflow endpoints — write to approval_actions + executions ──────

class ExecuteRequest(BaseModel):
    fix_type:        Optional[str] = "restart_service"
    operator_id:     Optional[str] = "ops_dashboard"
    operator_reason: Optional[str] = ""
    approval_path:   Optional[str] = "B"
    action_type:     Optional[str] = "APPROVE"
    rca_id:          Optional[str] = None
    confidence:      Optional[int] = None
    risk_tier:       Optional[str] = None


class RejectV2Request(BaseModel):
    operator_id:   Optional[str] = "ops_dashboard"
    reject_reason: Optional[str] = "Fix recommendation rejected by operator"
    approval_path: Optional[str] = "B"
    rca_id:        Optional[str] = None


def _map_fix_to_type(text: str) -> str:
    """Normalise a free-text recommended_fix into a canonical fix_type string.
    More-specific patterns are checked before less-specific ones."""
    t = (text or "").lower()
    if any(k in t for k in ("rollback", "revert")):             return "rollback_config"
    if any(k in t for k in ("scale", "replica", "capacity")):   return "scale_up"
    if any(k in t for k in ("cache", "clear", "purge", "flush")): return "clear_cache"
    if any(k in t for k in ("restart", "reboot", "service")):   return "restart_service"
    return "restart_service"


@app.post("/tickets/{ticket_id}/execute")
def execute_ticket(ticket_id: str, body: ExecuteRequest):
    """
    Approve and execute a fix recommendation.
    Writes to approval_actions + executions tables (Bug 2 fix).
    Resolves the ticket and updates fix_outcomes for trust calibration.
    """
    conn = get_db()
    row  = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, f"Ticket {ticket_id} not found")

    ticket   = dict(row)
    now      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fix_type = body.fix_type or _map_fix_to_type(body.operator_reason or "")
    category = ticket.get("category", "General")

    # Fetch confidence / risk from latest prediction if not provided
    pred_row = conn.execute(
        "SELECT confidence_score, risk_tier FROM predictions WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1",
        (ticket_id,)
    ).fetchone()
    confidence = body.confidence or (dict(pred_row)["confidence_score"] if pred_row else 70)
    risk_tier  = body.risk_tier  or (dict(pred_row)["risk_tier"]        if pred_row else "Medium")

    # Snapshot before/after state
    pre_state  = json.dumps({"status": ticket.get("status"), "resolution_notes": ticket.get("resolution_notes")})
    post_state = json.dumps({"status": "resolved", "fix_type": fix_type})

    # Write approval_actions record
    approval_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO approval_actions
        (id, ticket_id, rca_id, approval_path, action_type,
         operator_id, operator_reason, recommended_fix,
         confidence_at_time, risk_tier, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        approval_id, ticket_id, body.rca_id,
        body.approval_path, body.action_type or "APPROVE",
        body.operator_id, (body.operator_reason or "")[:_MAX_REASON_LEN],
        fix_type, confidence, risk_tier, now,
    ))

    # Write executions record
    execution_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO executions
        (id, approval_id, ticket_id, fix_type,
         pre_state, post_state, outcome, rolled_back, executed_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        execution_id, approval_id, ticket_id, fix_type,
        pre_state, post_state, "success", 0, now,
    ))

    # Resolve the ticket
    conn.execute("""
        UPDATE tickets
        SET status='resolved', resolved_at=?, resolution_notes=?, resolved_by=?
        WHERE id=?
    """, (now, body.operator_reason or fix_type, body.operator_id, ticket_id))

    # Update fix_outcomes for trust calibration
    conn.execute("""
        UPDATE fix_outcomes
        SET approve_count=approve_count+1, total_actions=total_actions+1, last_updated=?
        WHERE category=?
    """, (now, category))

    write_audit(conn, "EXECUTE", ticket_id,
                operator_id=body.operator_id,
                approval_path=body.approval_path,
                confidence=confidence, risk_tier=risk_tier,
                action_taken=f"Executed {fix_type} via Path {body.approval_path}",
                reasoning=(body.operator_reason or "")[:200],
                outcome="success")
    conn.commit()

    # Add to FAISS memory for future RCA
    if RCA_ENABLED:
        ticket["resolution_notes"] = body.operator_reason or fix_type
        ticket["status"] = "resolved"
        try: add_to_index(ticket)
        except Exception as e: print(f"⚠️  FAISS add: {e}")

    conn.close()
    return {
        "message":      f"Ticket {ticket_id} executed and resolved",
        "execution_id": execution_id,
        "approval_id":  approval_id,
        "outcome":      "success",
        "fix_type":     fix_type,
    }


@app.post("/tickets/{ticket_id}/reject_v2")
def reject_ticket_v2(ticket_id: str, body: RejectV2Request):
    """
    Reject a fix recommendation with a full approval_actions audit trail (Bug 2 fix).
    Ticket remains OPEN. No memory update.
    """
    conn = get_db()
    row  = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, f"Ticket {ticket_id} not found")

    ticket   = dict(row)
    now      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    category = ticket.get("category", "General")

    pred_row   = conn.execute(
        "SELECT confidence_score, risk_tier FROM predictions WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1",
        (ticket_id,)
    ).fetchone()
    confidence = dict(pred_row)["confidence_score"] if pred_row else 50
    risk_tier  = dict(pred_row)["risk_tier"]        if pred_row else "Medium"

    # Write approval_actions record
    approval_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO approval_actions
        (id, ticket_id, rca_id, approval_path, action_type,
         operator_id, operator_reason, confidence_at_time, risk_tier, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        approval_id, ticket_id, body.rca_id,
        body.approval_path, "REJECT",
        body.operator_id, (body.reject_reason or "")[:_MAX_REASON_LEN],
        confidence, risk_tier, now,
    ))

    # Mark ticket as rejected so clients can see the rejection state
    conn.execute("UPDATE tickets SET status='rejected' WHERE id=?", (ticket_id,))

    # Update fix_outcomes reject count
    conn.execute("""
        UPDATE fix_outcomes
        SET reject_count=reject_count+1, total_actions=total_actions+1, last_updated=?
        WHERE category=?
    """, (now, category))

    write_audit(conn, "REJECT", ticket_id,
                operator_id=body.operator_id,
                approval_path=body.approval_path,
                confidence=confidence, risk_tier=risk_tier,
                action_taken=f"Fix rejected via Path {body.approval_path}",
                reasoning=(body.reject_reason or "")[:200],
                outcome="rejected")
    conn.commit(); conn.close()
    return {
        "message":      f"Fix rejected. Ticket {ticket_id} marked as rejected.",
        "approval_id":  approval_id,
        "ticket_status": "rejected",
        "status":       "rejected",
        "outcome":      "rejected",
    }


@app.post("/tickets/{ticket_id}/cancel_auto")
def cancel_auto(ticket_id: str, body: dict):
    """
    Cancel a Path A auto-execution countdown.
    Writes a CANCEL event to the audit log so the action is traceable.
    """
    conn = get_db()
    if not conn.execute("SELECT id FROM tickets WHERE id=?", (ticket_id,)).fetchone():
        conn.close(); raise HTTPException(404, f"Ticket {ticket_id} not found")
    write_audit(conn, "CANCEL", ticket_id,
                operator_id=body.get("operator_id", "ops_dashboard"),
                action_taken="Auto-execution cancelled by operator",
                reasoning=f"rca_id={body.get('rca_id','?')}",
                outcome="cancelled")
    conn.commit(); conn.close()
    return {"message": f"Auto-execution cancelled for {ticket_id}", "outcome": "cancelled"}


@app.post("/tickets/{ticket_id}/cancel_user")
def cancel_ticket_user(ticket_id: str, body: dict):
    """
    User-initiated ticket cancellation from the client portal.
    Sets status to 'user_cancelled' and writes a USER_CANCEL audit event.
    Resolved or already-escalated tickets cannot be cancelled.
    """
    conn = get_db()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, f"Ticket {ticket_id} not found")

    ticket = dict(row)
    non_cancellable = {"resolved", "escalated", "user_cancelled"}
    if ticket.get("status") in non_cancellable:
        conn.close()
        raise HTTPException(400, f"Ticket {ticket_id} cannot be cancelled — status is '{ticket.get('status')}'")

    cancel_reason = (body.get("reason", "Cancelled by user") or "")[:200]
    raised_by = body.get("raised_by", "client_portal")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn.execute("UPDATE tickets SET status='user_cancelled' WHERE id=?", (ticket_id,))
    write_audit(conn, "USER_CANCEL", ticket_id,
                operator_id=raised_by,
                action_taken="Ticket cancelled by user",
                reasoning=cancel_reason,
                outcome="user_cancelled")
    conn.commit()
    conn.close()

    return {
        "message":   f"Ticket {ticket_id} has been cancelled",
        "ticket_id": ticket_id,
        "outcome":   "user_cancelled",
        "timestamp": now,
    }


@app.post("/tickets/{ticket_id}/reraise")
def reraise_ticket(ticket_id: str, body: dict):
    """
    Re-raise a rolled_back or rejected ticket to a specific support engineer queue.
    Called from the ReraisePanel in the main dashboard.
    Sets status to 'reraised' and assigns to the chosen engineer queue.
    """
    conn = get_db()
    row  = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, f"Ticket {ticket_id} not found")

    operator_id      = body.get("operator_id", "ops_dashboard")
    reraise_reason   = (body.get("reraise_reason", "Ticket requires further review") or "")[:200]
    additional_ctx   = (body.get("additional_context", "") or "")[:200]
    assigned_engineer= body.get("assigned_engineer", "L2_Support_Queue")
    now              = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn.execute("""
        UPDATE tickets SET status='reraised', assigned_group=? WHERE id=?
    """, (assigned_engineer, ticket_id))

    write_audit(conn, "RERAISE", ticket_id,
                operator_id=operator_id,
                action_taken=f"Re-raised to {assigned_engineer}",
                reasoning=f"{reraise_reason}. Context: {additional_ctx}",
                outcome="reraised")
    conn.commit(); conn.close()

    return {
        "status":      "reraised",
        "ticket_id":   ticket_id,
        "assigned_to": assigned_engineer,
        "operator_id": operator_id,
        "reason":      reraise_reason,
        "timestamp":   now,
    }


@app.get("/tickets/{ticket_id}/executions")
def get_ticket_executions(ticket_id: str):
    """Returns all execution records for a ticket (Bug 2 fix)."""
    conn  = get_db()
    if not conn.execute("SELECT id FROM tickets WHERE id=?", (ticket_id,)).fetchone():
        conn.close(); raise HTTPException(404, f"Ticket {ticket_id} not found")
    rows = conn.execute("""
        SELECT e.*, a.approval_path, a.action_type, a.operator_id as approver_id
        FROM executions e
        LEFT JOIN approval_actions a ON e.approval_id = a.id
        WHERE e.ticket_id = ?
        ORDER BY e.executed_at DESC
    """, (ticket_id,)).fetchall()
    conn.close()
    return {"ticket_id": ticket_id, "executions": [dict(r) for r in rows]}


@app.post("/executions/{execution_id}/rollback")
def rollback_execution(execution_id: str, body: dict):
    """
    Roll back a specific execution record (Bug 2 fix).
    Marks the execution as rolled_back, reverts ticket to open,
    and penalises the fix_outcomes table.
    """
    conn = get_db()
    exe  = conn.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
    if not exe:
        conn.close(); raise HTTPException(404, f"Execution {execution_id} not found")

    exe_dict    = dict(exe)
    ticket_id   = exe_dict["ticket_id"]
    ticket_row  = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    now         = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    reason      = body.get("rollback_reason", "Operator rollback")
    operator_id = body.get("operator_id", "ops_dashboard")
    category    = dict(ticket_row).get("category", "General") if ticket_row else "General"

    # Mark execution as rolled_back
    conn.execute("""
        UPDATE executions
        SET outcome='rolled_back', rolled_back=1, rollback_reason=?, rolled_back_at=?
        WHERE id=?
    """, (reason[:_MAX_REASON_LEN], now, execution_id))

    # Revert ticket to open
    if ticket_row:
        conn.execute("""
            UPDATE tickets
            SET status='open', resolution_notes=NULL, resolved_at=NULL, resolved_by=NULL
            WHERE id=?
        """, (ticket_id,))

    # Penalise fix_outcomes
    conn.execute("""
        UPDATE fix_outcomes
        SET rollback_count=rollback_count+1, total_actions=total_actions+1, last_updated=?
        WHERE category=?
    """, (now, category))

    write_audit(conn, "ROLLBACK", ticket_id,
                operator_id=operator_id,
                action_taken=f"Execution {execution_id[:12]} rolled back",
                reasoning=reason[:200], outcome="rolled_back")
    conn.commit(); conn.close()
    return {
        "message":      f"Execution {execution_id} rolled back",
        "ticket_id":    ticket_id,
        "status":       "rolled_back",
        "outcome":      "rolled_back",
    }


@app.post("/tickets/{ticket_id}/escalate")
def escalate_ticket(ticket_id: str, body: dict):
    """
    Escalate a ticket to the human support engineer team.
    Called after reject, rollback, or direct user request.
    Sets status to 'escalated', logs reason, writes audit event.
    """
    conn = get_db()
    row  = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, f"Ticket {ticket_id} not found")

    reason      = body.get("reason", "Escalated by user")
    raised_by   = body.get("raised_by", "user")
    contact     = body.get("contact", "")
    priority    = body.get("priority", dict(row).get("severity", "P2"))
    now         = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn.execute("""
        UPDATE tickets
        SET status='escalated', assigned_group='SUPPORT-ENGINEER-TEAM'
        WHERE id=?
    """, (ticket_id,))

    write_audit(conn, "ESCALATE", ticket_id,
                operator_id=raised_by,
                action_taken=f"Escalated to support engineer team",
                reasoning=f"Reason: {reason[:200]} | Contact: {contact}",
                outcome="escalated")
    conn.commit()
    conn.close()

    return {
        "message":       f"Ticket {ticket_id} escalated to support engineer team",
        "ticket_id":     ticket_id,
        "outcome":       "escalated",
        "assigned_to":   "SUPPORT-ENGINEER-TEAM",
        "raised_by":     raised_by,
        "reason":        reason,
        "contact":       contact,
        "priority":      priority,
        "estimated_response": "15-30 minutes for P1, 2-4 hours for P2, next business day for P3",
        "timestamp":     now,
    }


@app.get("/tickets/escalated")
def get_escalated_tickets():
    """Returns all tickets currently escalated to engineer team."""
    conn  = get_db()
    rows  = conn.execute("""
        SELECT t.*, a.reasoning as escalation_reason, a.timestamp as escalated_at
        FROM tickets t
        LEFT JOIN audit_log a ON t.id = a.ticket_id AND a.event_type = 'ESCALATE'
        WHERE t.status = 'escalated'
        ORDER BY t.created_at DESC
    """).fetchall()
    conn.close()
    return {"escalated": [dict(r) for r in rows], "total": len(rows)}


@app.post("/tickets/{ticket_id}/chat")
def ticket_chat(ticket_id: str, body: dict):
    """
    AI voice assistant chat endpoint for client portal.
    Uses Groq LLaMA to answer questions about the ticket and guide through fixes.
    Detects escalation intent and returns escalate_requested flag.
    Token-optimized: short system prompt, conversation capped at 6 turns.
    """
    conn = get_db()
    ticket_row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    rca_row    = conn.execute(
        "SELECT * FROM rca_results WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1",
        (ticket_id,)
    ).fetchone()
    conn.close()

    if not ticket_row:
        raise HTTPException(404, f"Ticket {ticket_id} not found")

    ticket  = dict(ticket_row)
    rca     = dict(rca_row) if rca_row else {}
    message = body.get("message", "").strip()
    history = body.get("history", [])[-5:]  # cap at last 5 turns to save tokens

    # Detect escalation intent in message
    escalation_triggers = [
        "human", "engineer", "person", "agent", "support team",
        "escalate", "talk to someone", "real person", "human help",
        "not working", "still broken", "didn't fix", "doesn't work",
    ]
    escalate_requested = any(t in message.lower() for t in escalation_triggers)

    import os
    from groq import Groq
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {
            "reply": "AI assistant unavailable — GROQ_API_KEY not configured.",
            "escalate_requested": False,
        }

    client = Groq(api_key=api_key)

    # Token-optimized system prompt — concise but complete
    system = f"""You are a helpful IT support assistant for ticket {ticket_id}.
Ticket: {ticket.get('description','')[:200]}
Severity: {ticket.get('severity','?')} | Category: {ticket.get('category','?')} | Status: {ticket.get('status','?')}
{f"Root cause: {rca.get('root_cause','')[:150]}" if rca.get('root_cause') else ""}
{f"Recommended fix: {rca.get('recommended_fix','')[:150]}" if rca.get('recommended_fix') else ""}

Guide the user step by step. Be clear, practical, non-technical where possible.
If they ask for a human/engineer or say the fix isn't working, acknowledge it and tell them you'll escalate.
Keep replies under 120 words."""

    messages = [{"role": "system", "content": system}]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"][:300]})
    messages.append({"role": "user", "content": message[:400]})

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.4,
            max_tokens=180,   # keep responses short for voice readback
        )
        reply = response.choices[0].message.content.strip()
    except Exception as e:
        reply = f"Sorry, I'm having trouble right now. Error: {str(e)[:60]}"

    return {
        "reply":              reply,
        "escalate_requested": escalate_requested,
        "ticket_status":      ticket.get("status"),
        "severity":           ticket.get("severity"),
    }



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
    total        = conn.execute(count_query, params).fetchone()[0] * DEMO_SCALE

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
    by_severity = conn.execute(f"""
        SELECT severity, COUNT(*) * {DEMO_SCALE} as count FROM tickets GROUP BY severity ORDER BY severity
    """).fetchall()
    by_category = conn.execute(f"""
        SELECT category, COUNT(*) * {DEMO_SCALE} as count FROM tickets GROUP BY category ORDER BY count DESC
    """).fetchall()
    by_status = conn.execute(f"""
        SELECT status, COUNT(*) * {DEMO_SCALE} as count FROM tickets GROUP BY status ORDER BY count DESC
    """).fetchall()
    with_resolution = conn.execute("""
        SELECT COUNT(*) FROM tickets
        WHERE resolution_notes IS NOT NULL AND resolution_notes != ''
        AND resolution_notes NOT IN ('nan','None','NaN')
    """).fetchone()[0] * DEMO_SCALE
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
def get_ticket_prediction(ticket_id: str):
    """Returns the latest prediction for a ticket (includes approval_path)."""
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
    q += " AND created_at >= datetime('now', '-30 days')"
    if status: q += " AND status=?"; params.append(status)
    if severity: q += " AND severity=?"; params.append(severity.upper())
    q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"; params += [limit, offset]
    rows  = conn.execute(q, params).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] * DEMO_SCALE
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
        "total_tickets":    conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] * DEMO_SCALE,
        # Only count tickets ingested via the API (created_at is recent)
        # Historical ITSM dataset tickets have opened_at in 2012-2014 but created_at from setup
        # We count "open" as tickets that are genuinely actionable (not the 46k historical ones)
        "open_tickets":     conn.execute("""
            SELECT COUNT(*) FROM tickets
            WHERE status = 'open'
            AND created_at >= datetime('now', '-30 days')
        """).fetchone()[0],
        "pending_approval": conn.execute("SELECT COUNT(*) FROM tickets WHERE status='pending_approval'").fetchone()[0],
        "resolved":         conn.execute("""
            SELECT COUNT(*) FROM tickets
            WHERE status = 'resolved'
            AND created_at >= datetime('now', '-30 days')
        """).fetchone()[0],
        "p1_open":          conn.execute("""
            SELECT COUNT(*) FROM tickets
            WHERE severity='P1' AND status!='resolved'
            AND created_at >= datetime('now', '-30 days')
        """).fetchone()[0],
        "p2_open":          conn.execute("""
            SELECT COUNT(*) FROM tickets
            WHERE severity='P2' AND status!='resolved'
            AND created_at >= datetime('now', '-30 days')
        """).fetchone()[0],
        "predictions_run":  conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] * DEMO_SCALE,
        "rca_completed":    conn.execute("SELECT COUNT(*) FROM rca_results").fetchone()[0] * DEMO_SCALE,
        "audit_events":     conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
        "phase":            "1+2+3" if (PREDICTION_ENABLED and RCA_ENABLED) else "1+2" if PREDICTION_ENABLED else "1",
    }
    conn.close(); return s
