"""
Phase 1+2+3 — FastAPI Ingestion Pipeline
==========================================
Phase 3 adds:
  POST /tickets/{id}/rca        — trigger RCA for a ticket
  GET  /tickets/{id}/rca/result — fetch stored RCA result
  POST /tickets/{id}/resolve    — mark resolved + add to FAISS memory

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

app = FastAPI(
    title="OpsAI API",
    description="Human-Governed Autonomous AI Support — Phase 1+2+3",
    version="3.0.0",
)
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
        INSERT INTO audit_log (id, event_type, ticket_id, action_taken, timestamp,
                               operator_id, confidence, risk_tier, reasoning, outcome)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (str(uuid.uuid4()), event_type, ticket_id, kwargs.get("action_taken",""),
          datetime.now().strftime("%Y-%m-%d %H:%M:%S"), kwargs.get("operator_id","system"),
          kwargs.get("confidence"), kwargs.get("risk_tier"),
          kwargs.get("reasoning",""), kwargs.get("outcome","")))


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

class ExecuteRequest(BaseModel):
    fix_type:         str = Field(..., description="restart_service|clear_cache|scale_up|rollback_config")
    approval_path:    str = Field(..., description="A|B|C")
    action_type:      str = Field(default="APPROVE", description="APPROVE|REJECT|OVERRIDE|AUTO")
    operator_id:      str = Field(default="operator")
    operator_reason:  Optional[str] = None
    confidence:       Optional[int] = None
    risk_tier:        Optional[str] = None


P1_KW  = ["production down","all users","data loss","complete outage","critical failure",
           "unresponsive","breach","all services","entire system","site down"]
P2_KW  = ["degraded","slow performance","intermittent","partial","some users","timeout",
           "high cpu","memory leak","disk full","connection refused"]
SEC_KW = ["unauthorized","breach","injection","exploit","malware","ransomware"]
CI_CAT_RISK = {"storage":"P2","hardware":"P1","network":"P1","application":"P2","subapplication":"P2"}

def keyword_analyze(description, ci_cat="", alert_status=""):
    desc = description.lower(); flags = []; suggested = None
    if str(alert_status).lower() == "true":
        flags.append("ACTIVE_ALERT"); suggested = "P1"
    ci_risk = CI_CAT_RISK.get(str(ci_cat).lower())
    if ci_risk and not suggested: flags.append(f"CI_CAT_RISK:{ci_cat}"); suggested = ci_risk
    for kw in P1_KW:
        if kw in desc: flags.append(f"P1:{kw}"); suggested = "P1"; break
    if suggested != "P1":
        for kw in P2_KW:
            if kw in desc: flags.append(f"P2:{kw}"); suggested = suggested or "P2"; break
    for kw in SEC_KW:
        if kw in desc: flags.append(f"SEC:{kw}"); suggested = "P1"
    return {"flags": flags, "suggested_severity": suggested, "anomaly_detected": bool(flags)}

def detect_category(description, ci_cat="", ci_subcat=""):
    cat_map = {"storage":"Database","database":"Database","network":"Network",
               "hardware":"Infrastructure","application":"Application","subapplication":"Application"}
    if ci_cat and ci_cat.lower() in cat_map: return cat_map[ci_cat.lower()]
    desc = (description + " " + ci_subcat).lower()
    kws = {"Database":["database","db","sql","storage"],"Network":["network","dns","vpn","firewall"],
           "Authentication":["login","auth","sso","password","token"],
           "Infrastructure":["server","cpu","memory","disk","container"],
           "Application":["application","api","app","web based"]}
    scores = {c: sum(1 for k in ws if k in desc) for c, ws in kws.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "General"

def normalize_severity(raw):
    m = {"1":"P1","2":"P2","3":"P3","4":"P3","5":"P3","p1":"P1","p2":"P2","p3":"P3",
         "critical":"P1","high":"P2","medium":"P3"}
    return m.get(str(raw).lower().strip(), "P3")

def bg_predict(ticket_id, description, category, ci_cat, ci_subcat, urgency, impact, alert_status):
    if not PREDICTION_ENABLED: return
    try:
        predict_ticket(ticket_id=ticket_id, description=description, category_hint=category,
                       ci_cat=ci_cat, ci_subcat=ci_subcat, category=category,
                       urgency=urgency, impact=impact, alert_status=alert_status)
    except Exception as e:
        print(f"⚠️  bg_predict ({ticket_id}): {e}")

def bg_rca(ticket_id):
    if not RCA_ENABLED: return
    try: run_rca(ticket_id)
    except Exception as e: print(f"⚠️  bg_rca ({ticket_id}): {e}")


@app.get("/health")
def health():
    return {"status": "ok",
            "phase": "1+2+3" if (PREDICTION_ENABLED and RCA_ENABLED) else "1+2" if PREDICTION_ENABLED else "1",
            "prediction_engine": PREDICTION_ENABLED, "rca_engine": RCA_ENABLED,
            "timestamp": datetime.now().isoformat()}


@app.post("/tickets/ingest", response_model=TicketResponse)
def ingest_ticket(ticket: TicketIngest, bg: BackgroundTasks):
    conn = get_db()
    tid  = f"INC{uuid.uuid4().hex[:8].upper()}"
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    kw   = keyword_analyze(ticket.description, ticket.ci_cat or "", ticket.alert_status or "")
    sev  = normalize_severity(ticket.severity) if ticket.severity else (kw["suggested_severity"] or "P3")
    cat  = ticket.category or detect_category(ticket.description, ticket.ci_cat or "", ticket.ci_subcat or "")
    conn.execute("INSERT INTO tickets (id,description,severity,category,opened_at,status,assigned_group) VALUES (?,?,?,?,?,?,?)",
                 (tid, ticket.description, sev, cat, now, "open", ticket.assigned_group or "UNASSIGNED"))
    write_audit(conn, "INGEST", tid, action_taken=f"via {ticket.source}", reasoning=str(kw["flags"]), outcome="created")
    conn.commit(); conn.close()
    bg.add_task(bg_predict, tid, ticket.description, cat,
                ticket.ci_cat or "", ticket.ci_subcat or "",
                ticket.urgency or "", ticket.impact or "", ticket.alert_status or "")
    if sev in ("P1","P2"):
        bg.add_task(bg_rca, tid)
    return TicketResponse(id=tid, description=ticket.description, severity=sev, category=cat,
                          status="open", opened_at=now, anomaly_flags=kw["flags"],
                          message=f"{tid} | {sev} | RCA {'auto-triggered' if sev in ('P1','P2') else 'on-demand'}")


@app.post("/tickets/{ticket_id}/rca")
def trigger_rca(ticket_id: str, bg: BackgroundTasks):
    if not RCA_ENABLED: raise HTTPException(503, "RCA engine not loaded")
    conn = get_db()
    if not conn.execute("SELECT id FROM tickets WHERE id=?", (ticket_id,)).fetchone():
        conn.close(); raise HTTPException(404, f"Ticket {ticket_id} not found")
    conn.close()
    bg.add_task(bg_rca, ticket_id)
    return {"message": f"RCA triggered for {ticket_id}. Fetch in ~5s.", "result_url": f"/tickets/{ticket_id}/rca/result"}


@app.get("/tickets/{ticket_id}/rca/result")
def get_rca_result(ticket_id: str):
    conn = get_db()
    row  = conn.execute("SELECT * FROM rca_results WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1", (ticket_id,)).fetchone()
    conn.close()
    if not row:
        return {"ticket_id": ticket_id, "status": "pending", "message": "RCA not ready. Retry in 4s."}
    result = dict(row)
    try: result["similarity_scores"] = json.loads(result.get("similarity_scores","[]"))
    except Exception: pass
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
                action_taken="resolved", reasoning=body.resolution_notes[:100], outcome="resolved")
    conn.commit()
    if RCA_ENABLED:
        ticket_dict = dict(row)
        ticket_dict["resolution_notes"] = body.resolution_notes
        ticket_dict["status"] = "resolved"
        try: add_to_index(ticket_dict)
        except Exception as e: print(f"⚠️  FAISS add failed: {e}")
    conn.close()
    return {"message": f"{ticket_id} resolved", "memory_updated": RCA_ENABLED}


# ── Remediation simulations ──────────────────────────────────────────────────
import time as _time

def _simulate_fix(fix_type: str, ticket: dict) -> dict:
    """Simulate executing a remediation action. Returns pre/post state."""
    pre_state = {
        "ticket_id": ticket["id"],
        "status": ticket["status"],
        "severity": ticket["severity"],
        "timestamp": datetime.now().isoformat(),
    }
    # Simulate work (real system would SSH/API call here)
    _time.sleep(0.5)
    post_state = {
        "ticket_id": ticket["id"],
        "status": "resolved",
        "fix_applied": fix_type,
        "outcome": "success",
        "timestamp": datetime.now().isoformat(),
    }
    return pre_state, post_state


@app.post("/tickets/{ticket_id}/execute")
def execute_fix(ticket_id: str, body: ExecuteRequest, bg: BackgroundTasks):
    """Full governance execution: approval → simulate fix → resolve → learn."""
    conn = get_db()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, f"Ticket {ticket_id} not found")
    ticket = dict(row)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Write to approval_actions
    approval_id = str(uuid.uuid4())
    rca_row = conn.execute(
        "SELECT id FROM rca_results WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1",
        (ticket_id,)
    ).fetchone()
    conn.execute("""
        INSERT INTO approval_actions
        (id, ticket_id, rca_id, approval_path, action_type, operator_id,
         operator_reason, recommended_fix, confidence_at_time, risk_tier, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        approval_id, ticket_id,
        rca_row["id"] if rca_row else None,
        body.approval_path, body.action_type, body.operator_id,
        body.operator_reason or "",
        body.fix_type, body.confidence, body.risk_tier, now,
    ))

    # 2. Simulate remediation
    pre_state, post_state = _simulate_fix(body.fix_type, ticket)

    # 3. Write to executions
    exec_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO executions
        (id, approval_id, ticket_id, fix_type, pre_state, post_state,
         outcome, rolled_back, executed_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        exec_id, approval_id, ticket_id, body.fix_type,
        json.dumps(pre_state), json.dumps(post_state),
        "success", 0, now,
    ))

    # 4. Update fix_outcomes trust calibration
    if body.action_type in ("APPROVE", "AUTO"):
        conn.execute("""
            UPDATE fix_outcomes SET approve_count = approve_count + 1,
            total_actions = total_actions + 1, last_updated = ?
            WHERE category = ? AND fix_type = ?
        """, (now, ticket["category"], body.fix_type))
    elif body.action_type == "REJECT":
        conn.execute("""
            UPDATE fix_outcomes SET reject_count = reject_count + 1,
            total_actions = total_actions + 1, last_updated = ?
            WHERE category = ? AND fix_type = ?
        """, (now, ticket["category"], body.fix_type))

    # 5. Resolve ticket
    conn.execute(
        "UPDATE tickets SET status='resolved', resolved_at=?, resolution_notes=?, resolved_by=? WHERE id=?",
        (now, f"Fix: {body.fix_type}", body.operator_id, ticket_id)
    )

    # 6. Audit log
    write_audit(conn, "EXECUTE", ticket_id,
                operator_id=body.operator_id,
                action_taken=f"{body.action_type} → {body.fix_type}",
                confidence=body.confidence,
                risk_tier=body.risk_tier,
                reasoning=body.operator_reason or f"Executed {body.fix_type}",
                outcome="success")
    conn.commit()

    # 7. Add to FAISS memory (learning loop)
    if RCA_ENABLED:
        resolved_ticket = dict(row)
        resolved_ticket["resolution_notes"] = f"Fix: {body.fix_type}"
        resolved_ticket["status"] = "resolved"
        try:
            add_to_index(resolved_ticket)
        except Exception as e:
            print(f"⚠️  FAISS add failed: {e}")
    conn.close()

    return {
        "message": f"{ticket_id} executed and resolved",
        "execution_id": exec_id,
        "approval_id": approval_id,
        "fix_type": body.fix_type,
        "outcome": "success",
        "memory_updated": RCA_ENABLED,
        "rollback_url": f"/executions/{exec_id}/rollback",
    }


@app.post("/executions/{execution_id}/rollback")
def rollback_execution(execution_id: str):
    """Rollback a previous execution — restores pre-state, penalizes confidence."""
    conn = get_db()
    row = conn.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, f"Execution {execution_id} not found")
    if row["rolled_back"]:
        conn.close()
        raise HTTPException(400, "Already rolled back")

    exec_data = dict(row)
    ticket_id = exec_data["ticket_id"]
    fix_type  = exec_data["fix_type"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Mark execution as rolled back
    conn.execute(
        "UPDATE executions SET rolled_back=1, rollback_reason='operator_initiated', rolled_back_at=? WHERE id=?",
        (now, execution_id)
    )

    # 2. Reopen ticket
    conn.execute(
        "UPDATE tickets SET status='open', resolved_at=NULL, resolution_notes=NULL WHERE id=?",
        (ticket_id,)
    )

    # 3. Penalize fix confidence in fix_outcomes
    ticket_row = conn.execute("SELECT category FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if ticket_row:
        conn.execute("""
            UPDATE fix_outcomes SET rollback_count = rollback_count + 1,
            total_actions = total_actions + 1, last_updated = ?
            WHERE category = ? AND fix_type = ?
        """, (now, ticket_row["category"], fix_type))

    # 4. Audit log
    write_audit(conn, "ROLLBACK", ticket_id,
                action_taken=f"Rolled back {fix_type}",
                reasoning="Operator-initiated rollback",
                outcome="rolled_back")
    conn.commit()
    conn.close()

    return {
        "message": f"Execution {execution_id} rolled back",
        "ticket_id": ticket_id,
        "ticket_status": "open",
        "fix_penalized": True,
    }


@app.get("/tickets/{ticket_id}/prediction")
def get_prediction(ticket_id: str):
    conn = get_db()
    row  = conn.execute("SELECT * FROM predictions WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1", (ticket_id,)).fetchone()
    conn.close()
    if not row: return {"ticket_id": ticket_id, "status": "pending", "message": "Retry in 3s."}
    return dict(row)

@app.get("/tickets")
def list_tickets(status: Optional[str]=None, severity: Optional[str]=None, limit: int=50, offset: int=0):
    conn=get_db(); q="SELECT * FROM tickets WHERE 1=1"; params=[]
    if status: q+=" AND status=?"; params.append(status)
    if severity: q+=" AND severity=?"; params.append(severity.upper())
    q+=" ORDER BY opened_at DESC LIMIT ? OFFSET ?"; params+=[limit,offset]
    rows=conn.execute(q,params).fetchall()
    total=conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    conn.close()
    return {"tickets":[dict(r) for r in rows],"total":total}

@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    conn=get_db(); row=conn.execute("SELECT * FROM tickets WHERE id=?",(ticket_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404, f"Ticket {ticket_id} not found")
    return dict(row)

@app.get("/tickets/{ticket_id}/audit")
def get_ticket_audit(ticket_id: str):
    conn=get_db()
    rows=conn.execute("SELECT * FROM audit_log WHERE ticket_id=? ORDER BY timestamp ASC",(ticket_id,)).fetchall()
    conn.close()
    return {"ticket_id":ticket_id,"events":[dict(r) for r in rows]}

@app.get("/audit")
def list_audit(limit: int=100, event_type: Optional[str]=None):
    conn=get_db(); q="SELECT * FROM audit_log WHERE 1=1"; params=[]
    if event_type: q+=" AND event_type=?"; params.append(event_type.upper())
    q+=" ORDER BY timestamp DESC LIMIT ?"; params.append(limit)
    rows=conn.execute(q,params).fetchall(); conn.close()
    return {"events":[dict(r) for r in rows],"total":len(rows)}

@app.get("/stats")
def get_stats():
    conn=get_db()
    s={"total_tickets":conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0],
       "open_tickets":conn.execute("SELECT COUNT(*) FROM tickets WHERE status='open'").fetchone()[0],
       "pending_approval":conn.execute("SELECT COUNT(*) FROM tickets WHERE status='pending_approval'").fetchone()[0],
       "resolved":conn.execute("SELECT COUNT(*) FROM tickets WHERE status='resolved'").fetchone()[0],
       "p1_open":conn.execute("SELECT COUNT(*) FROM tickets WHERE severity='P1' AND status IN ('open', 'pending_approval')").fetchone()[0],
       "predictions_run":conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0],
       "rca_completed":conn.execute("SELECT COUNT(*) FROM rca_results").fetchone()[0],
       "audit_events":conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
       "phase":"1+2+3" if (PREDICTION_ENABLED and RCA_ENABLED) else "1+2" if PREDICTION_ENABLED else "1"}
    conn.close(); return s
