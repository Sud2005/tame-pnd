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
import os
import sqlite3
import tempfile
import time
import uuid
from datetime import datetime, timezone
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
         operator_id, confidence, risk_tier, reasoning, outcome, approval_path)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        str(uuid.uuid4()), event_type, ticket_id,
        kwargs.get("action_taken", ""),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        kwargs.get("operator_id", "system"),
        kwargs.get("confidence"),
        kwargs.get("risk_tier"),
        kwargs.get("reasoning", ""),
        kwargs.get("outcome", ""),
        kwargs.get("approval_path"),
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


class ExecuteRequest(BaseModel):
    fix_type:        str
    operator_id:     str = "ops_dashboard"
    operator_reason: str = ""
    approval_path:   str = "B"
    rca_id:          Optional[str] = None


class ExecutionRollbackRequest(BaseModel):
    operator_id:     str = "ops_dashboard"
    rollback_reason: str = Field(..., min_length=3)


class RejectRequestV2(BaseModel):
    operator_id:    str = "ops_dashboard"
    reject_reason:  str = Field(..., min_length=3)
    approval_path:  str = "B"
    rca_id:         Optional[str] = None


class ReraiseRequest(BaseModel):
    operator_id:        str = "ops_dashboard"
    reraise_reason:     str = Field(..., min_length=3)
    additional_context: str = ""
    assigned_engineer:  str = "L2_Support_Queue"


class CancelAutoRequest(BaseModel):
    operator_id: str = "ops_dashboard"
    rca_id:      Optional[str] = None


# ── Helper functions ─────────────────────────────────────────────────────────
def gen_id(prefix=""):
    return f"{prefix}{uuid.uuid4().hex[:8].upper()}"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _map_fix_to_type(text: str) -> str:
    """Map free-text recommended_fix to a simulation fix_type via keywords."""
    t = (text or "").lower()
    if any(k in t for k in ["restart", "reboot", "service"]):
        return "restart_service"
    if any(k in t for k in ["cache", "clear", "purge", "flush"]):
        return "clear_cache"
    if any(k in t for k in ["scale", "replica", "capacity", "horizontal"]):
        return "scale_up"
    if any(k in t for k in ["rollback", "revert", "undo", "previous", "config"]):
        return "rollback_config"
    return "restart_service"


def _build_pre_state(ticket_row: dict) -> dict:
    """Build a standardised pre_state snapshot from a ticket row."""
    return {
        "status":         ticket_row.get("status", "open"),
        "severity":       ticket_row.get("severity", "P3"),
        "category":       ticket_row.get("category", "General"),
        "service_status": "degraded",
        "replicas":       1,
        "cache_size_mb":  128,
        "restart_count":  0,
        "snapshot_at":    now_iso(),
    }


# ── Remediation simulations ──────────────────────────────────────────────────
def sim_restart_service(ticket_id: str, pre_state: dict) -> dict:
    time.sleep(2)
    post_state = {
        **pre_state,
        "service_status": "running",
        "restart_count":  pre_state.get("restart_count", 0) + 1,
        "last_restart":   now_iso(),
    }
    return {"success": True, "message": f"Service restarted for {ticket_id}. Status: running.", "post_state": post_state}


def sim_clear_cache(ticket_id: str, pre_state: dict) -> dict:
    tmp_path = os.path.join(tempfile.gettempdir(), f"cache_{ticket_id}.tmp")
    try:
        with open(tmp_path, "w") as f:
            f.write(f"cache_data_{ticket_id}")
        os.remove(tmp_path)
    except Exception:
        pass
    freed = pre_state.get("cache_size_mb", 128)
    post_state = {**pre_state, "cache_size_mb": 0, "freed_mb": freed}
    return {"success": True, "message": f"Cache cleared for {ticket_id}. Freed {freed}MB.", "post_state": post_state}


def sim_scale_up(ticket_id: str, db_path: str, pre_state: dict) -> dict:
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS sim_config(key TEXT PRIMARY KEY, value INTEGER, updated_at TEXT)")
    config_key = f"replicas_{ticket_id[:8]}"
    conn.execute("INSERT OR IGNORE INTO sim_config(key, value, updated_at) VALUES(?, 1, ?)", (config_key, now_iso()))
    conn.execute("UPDATE sim_config SET value = value + 1, updated_at = ? WHERE key = ?", (now_iso(), config_key))
    conn.commit()
    new_val = conn.execute("SELECT value FROM sim_config WHERE key=?", (config_key,)).fetchone()[0]
    conn.close()
    post_state = {**pre_state, "replicas": new_val, "service_status": "running"}
    return {"success": True, "message": f"Scaled up {ticket_id}. Replicas: {new_val}.", "post_state": post_state}


def sim_rollback_config(ticket_id: str, db_path: str, pre_state: dict) -> dict:
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS sim_config(key TEXT PRIMARY KEY, value INTEGER, updated_at TEXT)")
    config_key = f"replicas_{ticket_id[:8]}"
    restore_val = pre_state.get("replicas", 1)
    conn.execute("INSERT OR IGNORE INTO sim_config(key, value, updated_at) VALUES(?, ?, ?)", (config_key, restore_val, now_iso()))
    conn.execute("UPDATE sim_config SET value = ?, updated_at = ? WHERE key = ?", (restore_val, now_iso(), config_key))
    conn.commit()
    conn.close()
    post_state = {**pre_state, "replicas": restore_val, "service_status": "restored"}
    return {"success": True, "message": f"Config rolled back for {ticket_id}. Replicas: {restore_val}.", "post_state": post_state}


def _run_sim(fix_type: str, ticket_id: str, db_path: str, pre_state: dict) -> dict:
    """Dispatch to the correct simulation function."""
    dispatch = {
        "restart_service":  lambda: sim_restart_service(ticket_id, pre_state),
        "clear_cache":      lambda: sim_clear_cache(ticket_id, pre_state),
        "scale_up":         lambda: sim_scale_up(ticket_id, db_path, pre_state),
        "rollback_config":  lambda: sim_rollback_config(ticket_id, db_path, pre_state),
    }
    fn = dispatch.get(fix_type, dispatch["restart_service"])
    return fn()


def _upsert_fix_outcome(conn, category: str, fix_type: str, approve_delta=0, reject_delta=0, rollback_delta=0, conf_delta=0):
    """Upsert fix_outcomes row: increment counters and adjust calibrated_confidence."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        INSERT INTO fix_outcomes (id, category, fix_type, approve_count, reject_count, rollback_count, total_actions, calibrated_confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, 50)
        ON CONFLICT(category, fix_type) DO UPDATE SET
            approve_count  = approve_count  + ?,
            reject_count   = reject_count   + ?,
            rollback_count = rollback_count  + ?,
            total_actions  = total_actions   + ?,
            calibrated_confidence = MAX(10, MIN(95, calibrated_confidence + ?))
    """, (
        gen_id("FO"), category, fix_type,
        approve_delta, reject_delta, rollback_delta,
        approve_delta + reject_delta + rollback_delta,
        approve_delta, reject_delta, rollback_delta,
        approve_delta + reject_delta + rollback_delta,
        conf_delta,
    ))


# ── Build detailed resolution notes from RCA data ─────────────────────────────
def _build_detailed_resolution(rca_dict: dict, sim_message: str, fix_type: str) -> str:
    """Build comprehensive resolution notes from RCA fields + simulation result."""
    parts = []
    root_cause = rca_dict.get("root_cause", "")
    rec_fix = rca_dict.get("recommended_fix", "")
    fix_steps_raw = rca_dict.get("fix_steps", "")
    pattern = rca_dict.get("pattern_match", "")
    warnings = rca_dict.get("warnings", "")

    # Parse fix_steps if stored as JSON string
    if isinstance(fix_steps_raw, str):
        try:
            fix_steps_raw = json.loads(fix_steps_raw)
        except Exception:
            fix_steps_raw = [fix_steps_raw] if fix_steps_raw else []
    if not isinstance(fix_steps_raw, list):
        fix_steps_raw = [str(fix_steps_raw)] if fix_steps_raw else []

    parts.append(f"ROOT CAUSE: {root_cause}")
    if rec_fix:
        parts.append(f"\nRECOMMENDED FIX: {rec_fix}")
    if fix_steps_raw:
        steps_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(fix_steps_raw))
        parts.append(f"\nFIX STEPS:\n{steps_text}")
    if pattern:
        parts.append(f"\nPATTERN MATCH: {pattern}")
    if warnings and str(warnings).lower() not in ("null", "none", ""):
        parts.append(f"\nWARNINGS: {warnings}")
    parts.append(f"\nEXECUTION RESULT: {sim_message}")
    parts.append(f"FIX TYPE: {fix_type}")

    return "\n".join(parts)


# ── Auto-approve for low-risk Path A tickets ─────────────────────────────────
def auto_approve_low_risk(ticket_id: str, db_path: str):
    """Called at end of bg_rca(). Uses RCA confidence (always available at this point).
    If P3 + conf >= 45 + risk != Critical → auto-execute without human."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 1. Fetch ticket — skip if already resolved
    ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not ticket:
        conn.close(); return
    ticket = dict(ticket)
    if ticket.get("status") not in ("open", "pending_approval"):
        conn.close(); return  # already resolved/rejected/etc
    sev = ticket.get("severity", "P3")

    # P1 always needs human review
    if sev == "P1":
        conn.close(); return

    # 2. Fetch RCA (primary confidence source — always available since called from bg_rca)
    rca = conn.execute("SELECT * FROM rca_results WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1", (ticket_id,)).fetchone()
    if not rca:
        conn.close(); return
    rca_dict = dict(rca)
    rca_conf = int(rca_dict.get("confidence_score", 0))
    rca_risk = str(rca_dict.get("risk_tier", "Medium"))
    rca_id = rca_dict.get("id", "")

    # 3. Also check prediction confidence (may or may not be available yet)
    pred = conn.execute("SELECT * FROM predictions WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1", (ticket_id,)).fetchone()
    pred_conf = int(dict(pred).get("confidence_score", 0)) if pred else 0
    pred_risk = dict(pred).get("risk_tier", rca_risk) if pred else rca_risk

    # Use highest confidence between RCA and prediction
    conf = max(rca_conf, pred_conf)
    risk = rca_risk  # RCA risk is more accurate (calibrated with similarity data)

    # 4. Gating: simplified auto-approve criteria
    #    P3 + conf >= 40 + risk != Critical → auto-approve
    #    P2 + conf >= 55 + risk != Critical → auto-approve
    can_auto = False
    if sev == "P3" and conf >= 40 and risk != "Critical":
        can_auto = True
    elif sev == "P2" and conf >= 55 and risk != "Critical":
        can_auto = True

    if not can_auto:
        conn.close()
        print(f"[AUTO-APPROVE] {ticket_id} skipped: sev={sev} conf={conf} risk={risk}")
        return

    # 5. Build fix
    rec_fix = rca_dict.get("recommended_fix", "")
    fix_type = _map_fix_to_type(rec_fix)
    pre_state = _build_pre_state(ticket)

    # 6. Insert approval_actions
    approval_id = gen_id("APR")
    conn.execute("""
        INSERT INTO approval_actions (id, ticket_id, rca_id, approval_path, action_type, operator_id, operator_reason, recommended_fix, confidence_at_time, risk_tier)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (approval_id, ticket_id, rca_id, "A", "AUTO", "system",
          "Automatically approved — Low risk ticket, confidence above threshold per governance policy",
          rec_fix, conf, risk))

    # 7. Run simulation
    result = _run_sim(fix_type, ticket_id, db_path, pre_state)

    # 8. Insert execution
    exec_id = gen_id("EXE")
    outcome = "success" if result["success"] else "failed"
    conn.execute("""
        INSERT INTO executions (id, approval_id, ticket_id, fix_type, pre_state, post_state, outcome, rolled_back, executed_at)
        VALUES (?,?,?,?,?,?,?,0,?)
    """, (exec_id, approval_id, ticket_id, fix_type,
          json.dumps(pre_state), json.dumps(result["post_state"]),
          outcome, now_iso()))

    # 9. Build detailed resolution notes from RCA
    detailed_notes = _build_detailed_resolution(rca_dict, result["message"], fix_type)

    # 10. Resolve ticket
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE tickets SET status='resolved', resolved_at=?, resolution_notes=?, resolved_by='system' WHERE id=?",
                 (ts, detailed_notes, ticket_id))

    # 11. Upsert fix_outcomes
    category = ticket.get("category", "General")
    _upsert_fix_outcome(conn, category, fix_type, approve_delta=1, conf_delta=2)

    # 12. Audit log
    write_audit(conn, "AUTO_APPROVE", ticket_id,
                operator_id="system", action_taken=f"AUTO_EXECUTE:{fix_type}",
                confidence=conf, risk_tier=risk, reasoning="Low risk + high confidence auto-approval",
                outcome=outcome)
    conn.commit()
    conn.close()

    # 13. Add to FAISS memory with detailed notes
    if RCA_ENABLED:
        try:
            ticket["resolution_notes"] = detailed_notes
            ticket["status"] = "resolved"
            add_to_index(ticket)
        except Exception as e:
            print(f"⚠️  FAISS add (auto): {e}")

    print(f"[AUTO-APPROVE] {ticket_id} auto-approved → {fix_type} → {outcome}")


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
    # After RCA completes, check if auto-approve applies
    try:
        auto_approve_low_risk(ticket_id, DB_PATH)
    except Exception as e:
        print(f"⚠️  auto_approve ({ticket_id}): {e}")


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
    if RCA_ENABLED:
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
    if sev == "P1":
        approval_path = "C"
    elif sev == "P3" and conf >= 70:
        approval_path = "A"
    elif sev == "P2" and conf >= 85 and risk != "Critical":
        approval_path = "A"
    elif sev == "P3" and conf >= 40:
        approval_path = "B"
    elif sev == "P2" and conf >= 50:
        approval_path = "B"
    elif conf < 40:
        approval_path = "C"
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

    # Build detailed notes from RCA if available
    rca_row = conn.execute("SELECT * FROM rca_results WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1", (ticket_id,)).fetchone()
    if rca_row:
        detailed_notes = _build_detailed_resolution(dict(rca_row), body.resolution_notes, "manual")
    else:
        detailed_notes = body.resolution_notes

    conn.execute("UPDATE tickets SET status='resolved',resolved_at=?,resolution_notes=?,resolved_by=? WHERE id=?",
                 (now, detailed_notes, body.resolved_by, ticket_id))
    write_audit(conn, "RESOLVE", ticket_id, operator_id=body.resolved_by,
                action_taken="Approved and executed", reasoning=body.resolution_notes[:100], outcome="resolved")
    conn.commit()
    if RCA_ENABLED:
        ticket_dict = dict(row)
        ticket_dict["resolution_notes"] = detailed_notes
        ticket_dict["status"] = "resolved"
        try: add_to_index(ticket_dict)
        except Exception as e: print(f"⚠️  FAISS add: {e}")
    conn.close()
    return {"message": f"{ticket_id} resolved", "outcome": "success", "memory_updated": RCA_ENABLED, "ticket_status": "resolved"}


@app.post("/tickets/{ticket_id}/reject")
def reject_ticket(ticket_id: str, body: RejectRequest):
    """Reject recommended fix (legacy). Ticket stays OPEN. No memory update."""
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
    """Rollback executed fix (legacy). Reverts ticket to rolled_back. Penalises fix confidence."""
    conn = get_db()
    row  = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row: conn.close(); raise HTTPException(404, f"Ticket {ticket_id} not found")
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE tickets SET status='rolled_back',resolution_notes=?,resolved_at=NULL,resolved_by=NULL WHERE id=?",
                 (f"ROLLED BACK: {body.reason}", ticket_id))
    write_audit(conn, "ROLLBACK", ticket_id, operator_id=body.rolled_back_by,
                action_taken="Fix rolled back", reasoning=body.reason[:200], outcome="rolled_back")
    category = dict(row).get("category", "General")
    conn.execute("""
        UPDATE fix_outcomes SET rollback_count=rollback_count+1, total_actions=total_actions+1, last_updated=?
        WHERE category=?
    """, (now, category))
    conn.commit(); conn.close()
    return {"message": f"Ticket {ticket_id} rolled back.", "outcome": "rolled_back", "ticket_status": "rolled_back"}


# ── NEW ENDPOINTS: Execute, Rollback Execution, Reject V2, Reraise, Executions ──

@app.post("/tickets/{ticket_id}/execute")
def execute_ticket(ticket_id: str, req: ExecuteRequest):
    """Execute a fix via remediation simulation. Writes to approval_actions + executions."""
    conn = get_db()
    # 1. Fetch ticket
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, f"Ticket {ticket_id} not found")
    ticket = dict(row)

    # 2. Fetch latest prediction for conf + risk
    pred_row = conn.execute("SELECT * FROM predictions WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1", (ticket_id,)).fetchone()
    conf = int(dict(pred_row).get("confidence_score", 50)) if pred_row else 50
    risk = dict(pred_row).get("risk_tier", "Medium") if pred_row else "Medium"

    # 3. Build pre_state
    pre_state = _build_pre_state(ticket)

    # 4. Insert approval_actions
    approval_id = gen_id("APR")
    action_type = "AUTO" if req.approval_path == "A" else "APPROVE"
    conn.execute("""
        INSERT INTO approval_actions (id, ticket_id, rca_id, approval_path, action_type, operator_id, operator_reason, recommended_fix, confidence_at_time, risk_tier)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (approval_id, ticket_id, req.rca_id or "", req.approval_path, action_type,
          req.operator_id, req.operator_reason, req.fix_type, conf, risk))

    # 5. Run simulation
    result = _run_sim(req.fix_type, ticket_id, DB_PATH, pre_state)
    outcome = "success" if result["success"] else "failed"

    # 6. Insert execution
    exec_id = gen_id("EXE")
    conn.execute("""
        INSERT INTO executions (id, approval_id, ticket_id, fix_type, pre_state, post_state, outcome, rolled_back, executed_at)
        VALUES (?,?,?,?,?,?,?,0,?)
    """, (exec_id, approval_id, ticket_id, req.fix_type,
          json.dumps(pre_state), json.dumps(result["post_state"]),
          outcome, now_iso()))

    # 7. Build detailed resolution notes from RCA
    rca_row = conn.execute("SELECT * FROM rca_results WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1", (ticket_id,)).fetchone()
    if rca_row:
        detailed_notes = _build_detailed_resolution(dict(rca_row), result["message"], req.fix_type)
    else:
        detailed_notes = result["message"]

    # 8. Resolve ticket
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE tickets SET status='resolved', resolved_at=?, resolution_notes=?, resolved_by=? WHERE id=?",
                 (ts, detailed_notes, req.operator_id, ticket_id))

    # 8. Upsert fix_outcomes
    category = ticket.get("category", "General")
    _upsert_fix_outcome(conn, category, req.fix_type, approve_delta=1, conf_delta=2)

    # 9. Audit log
    write_audit(conn, "EXECUTE", ticket_id,
                operator_id=req.operator_id, action_taken=f"EXECUTE:{req.fix_type}",
                confidence=conf, risk_tier=risk, reasoning=req.operator_reason[:200],
                outcome=outcome, approval_path=req.approval_path)
    conn.commit()

    # 11. Add to FAISS
    if RCA_ENABLED:
        ticket["resolution_notes"] = detailed_notes
        ticket["status"] = "resolved"
        try: add_to_index(ticket)
        except Exception as e: print(f"⚠️  FAISS add: {e}")
    conn.close()

    return {
        "status": "ok", "execution_id": exec_id, "approval_id": approval_id,
        "outcome": outcome, "message": result["message"],
        "pre_state": pre_state, "post_state": result["post_state"],
    }


@app.post("/executions/{execution_id}/rollback")
def rollback_execution(execution_id: str, req: ExecutionRollbackRequest):
    """Rollback a specific execution. Restores pre_state, penalises confidence."""
    conn = get_db()
    # 1. Fetch execution
    exe_row = conn.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
    if not exe_row:
        conn.close(); raise HTTPException(404, f"Execution {execution_id} not found")
    exe = dict(exe_row)
    if exe.get("rolled_back") == 1:
        conn.close(); raise HTTPException(409, f"Execution {execution_id} already rolled back")

    ticket_id = exe["ticket_id"]
    fix_type = exe.get("fix_type", "restart_service")

    # 2. Deserialise pre_state
    try:
        pre_state = json.loads(exe.get("pre_state", "{}"))
    except Exception:
        pre_state = {}

    # 3. Run rollback simulation
    if fix_type == "scale_up":
        result = sim_rollback_config(ticket_id, DB_PATH, pre_state)
    else:
        result = _run_sim(fix_type, ticket_id, DB_PATH, pre_state)

    # 4. Update execution row
    conn.execute("""
        UPDATE executions SET rolled_back=1, rolled_back_at=?, rollback_reason=?, outcome='rolled_back', post_state=?
        WHERE id=?
    """, (now_iso(), req.rollback_reason, json.dumps(result["post_state"]), execution_id))

    # 5. Penalise fix_outcomes
    ticket_row = conn.execute("SELECT category FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    category = dict(ticket_row).get("category", "General") if ticket_row else "General"
    _upsert_fix_outcome(conn, category, fix_type, rollback_delta=1, conf_delta=-10)

    # 6. Revert ticket status
    conn.execute("UPDATE tickets SET status='rolled_back', resolved_at=NULL, resolution_notes=? WHERE id=?",
                 (f"ROLLED BACK: {req.rollback_reason}", ticket_id))

    # 7. Audit log — also fetch approval_path from the original approval_actions row
    apr_row = conn.execute("SELECT approval_path FROM approval_actions WHERE id=?", (exe.get("approval_id",""),)).fetchone()
    approval_path = apr_row["approval_path"] if apr_row else None
    write_audit(conn, "ROLLBACK", ticket_id,
                operator_id=req.operator_id, action_taken=f"ROLLBACK:{fix_type}",
                reasoning=req.rollback_reason[:200], outcome="rolled_back",
                approval_path=approval_path)
    conn.commit()
    conn.close()

    return {
        "status": "rolled_back", "execution_id": execution_id, "ticket_id": ticket_id,
        "ticket_status": "rolled_back", "message": result["message"],
        "pre_state": pre_state, "post_state": result["post_state"],
    }


@app.post("/tickets/{ticket_id}/reject_v2")
def reject_ticket_v2(ticket_id: str, req: RejectRequestV2):
    """Reject fix with full approval_actions trail and confidence penalty."""
    conn = get_db()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, f"Ticket {ticket_id} not found")
    ticket = dict(row)

    # Fetch prediction + rca context
    pred_row = conn.execute("SELECT * FROM predictions WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1", (ticket_id,)).fetchone()
    conf = int(dict(pred_row).get("confidence_score", 50)) if pred_row else 50
    risk = dict(pred_row).get("risk_tier", "Medium") if pred_row else "Medium"

    rca_row = conn.execute("SELECT * FROM rca_results WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1", (ticket_id,)).fetchone()
    rec_fix = dict(rca_row).get("recommended_fix", "") if rca_row else ""
    fix_type = _map_fix_to_type(rec_fix)

    # Insert approval_actions
    approval_id = gen_id("APR")
    conn.execute("""
        INSERT INTO approval_actions (id, ticket_id, rca_id, approval_path, action_type, operator_id, operator_reason, recommended_fix, confidence_at_time, risk_tier)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (approval_id, ticket_id, req.rca_id or "", req.approval_path, "REJECT",
          req.operator_id, req.reject_reason, rec_fix, conf, risk))

    # Penalise fix_outcomes
    category = ticket.get("category", "General")
    _upsert_fix_outcome(conn, category, fix_type, reject_delta=1, conf_delta=-5)

    # Update ticket status
    conn.execute("UPDATE tickets SET status='rejected' WHERE id=?", (ticket_id,))

    # Audit log
    write_audit(conn, "REJECT", ticket_id,
                operator_id=req.operator_id, action_taken="Fix rejected",
                confidence=conf, risk_tier=risk,
                reasoning=req.reject_reason[:200], outcome="rejected",
                approval_path=req.approval_path)
    conn.commit()
    conn.close()

    return {"status": "rejected", "approval_id": approval_id, "ticket_id": ticket_id, "ticket_status": "rejected"}


@app.post("/tickets/{ticket_id}/reraise")
def reraise_ticket(ticket_id: str, req: ReraiseRequest):
    """Re-raise a rolled_back or rejected ticket to an engineer queue."""
    conn = get_db()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, f"Ticket {ticket_id} not found")
    ticket = dict(row)
    status = ticket.get("status", "")
    if status not in ("rolled_back", "rejected"):
        conn.close()
        raise HTTPException(400, f"Ticket must be rolled_back or rejected to re-raise. Current status: {status}")

    # Enrich description
    desc = ticket.get("description", "")
    if req.additional_context.strip():
        desc += f"\n\n[RE-RAISED by {req.operator_id} — {now_iso()}]\nReason: {req.reraise_reason}\nAdditional context: {req.additional_context}"

    # Update ticket
    conn.execute("""
        UPDATE tickets SET status='pending_approval', description=?, assigned_group=?, resolved_at=NULL, resolution_notes=NULL WHERE id=?
    """, (desc, req.assigned_engineer, ticket_id))

    # Audit log
    write_audit(conn, "RERAISE", ticket_id,
                operator_id=req.operator_id, action_taken="ESCALATE_TO_ENGINEER",
                reasoning=req.reraise_reason[:200], outcome="reraised")
    conn.commit()
    conn.close()

    return {
        "status": "reraised", "ticket_id": ticket_id, "ticket_status": "pending_approval",
        "assigned_to": req.assigned_engineer,
        "message": f"Ticket {ticket_id} escalated to {req.assigned_engineer}. Engineer will review within SLA.",
    }


@app.post("/tickets/{ticket_id}/cancel_auto")
def cancel_auto_execution(ticket_id: str, req: CancelAutoRequest):
    """Log a Path A auto-execution cancellation to the audit trail.
    Called when the operator clicks CANCEL during the 10-second countdown window."""
    conn = get_db()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, f"Ticket {ticket_id} not found")

    # Fetch confidence + risk for context
    pred_row = conn.execute(
        "SELECT * FROM predictions WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1",
        (ticket_id,)
    ).fetchone()
    conf = int(dict(pred_row).get("confidence_score", 50)) if pred_row else 50
    risk = dict(pred_row).get("risk_tier", "Medium") if pred_row else "Medium"

    write_audit(conn, "AUTO_CANCEL", ticket_id,
                operator_id=req.operator_id,
                action_taken="AUTO_EXECUTE:cancelled_by_operator",
                confidence=conf, risk_tier=risk,
                reasoning="Operator cancelled Path A auto-execution during 10-second window",
                outcome="cancelled",
                approval_path="A")
    conn.commit()
    conn.close()

    return {"status": "cancelled", "ticket_id": ticket_id, "outcome": "cancelled"}


@app.get("/tickets/{ticket_id}/executions")
def get_ticket_executions(ticket_id: str):
    """List all executions for a ticket, latest first."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM executions WHERE ticket_id=? ORDER BY executed_at DESC", (ticket_id,)).fetchall()
    conn.close()
    return {"executions": [dict(r) for r in rows]}


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
    if not row:
        conn.close()
        return {"ticket_id": ticket_id, "status": "pending", "message": "Retry in 3s."}
    result = dict(row)

    # Derive approval_path from prediction data (not stored in DB)
    ticket_row = conn.execute("SELECT severity FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    sev  = dict(ticket_row).get("severity", "P3") if ticket_row else "P3"
    conn.close()
    conf = int(result.get("confidence_score") or 0)
    risk = str(result.get("risk_tier") or "Medium")

    if sev == "P1":
        approval_path = "C"
    elif sev == "P3" and conf >= 70:
        approval_path = "A"
    elif sev == "P2" and conf >= 85 and risk != "Critical":
        approval_path = "A"
    elif sev == "P3" and conf >= 40:
        approval_path = "B"
    elif sev == "P2" and conf >= 50:
        approval_path = "B"
    elif conf < 40:
        approval_path = "C"
    else:
        approval_path = "C"

    result["approval_path"] = approval_path
    return result


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
