"""
Phase 1 — Database Setup
==========================
Creates all SQLite tables and seeds them with clean ticket data.
Run this ONCE before starting the FastAPI server.

Usage:
    python db/setup_db.py --data data/tickets_clean.csv
"""

import argparse
import csv
import json
import sqlite3
import uuid
from datetime import datetime, timedelta
import random

DB_PATH = "db/opsai.db"

# ─── Schema ──────────────────────────────────────────────────────────────────

SCHEMA = """
-- Core tickets table
CREATE TABLE IF NOT EXISTS tickets (
    id                  TEXT PRIMARY KEY,
    description         TEXT NOT NULL,
    severity            TEXT NOT NULL,          -- P1 / P2 / P3
    category            TEXT NOT NULL,
    opened_at           TEXT,
    resolved_at         TEXT,
    resolution_time_hrs REAL,
    resolution_notes    TEXT,
    assigned_group      TEXT,
    resolved_by         TEXT,
    status              TEXT DEFAULT 'open',    -- open / pending_approval / resolved
    created_at          TEXT DEFAULT (datetime('now'))
);

-- Predictions made by the AI engine
CREATE TABLE IF NOT EXISTS predictions (
    id                  TEXT PRIMARY KEY,
    ticket_id           TEXT NOT NULL,
    predicted_severity  TEXT,
    predicted_category  TEXT,
    predicted_incident  TEXT,
    confidence_score    INTEGER,                -- 0-100
    risk_tier           TEXT,                   -- Low / Medium / Critical
    anomaly_flagged     INTEGER DEFAULT 0,      -- 0/1
    reasoning           TEXT,
    raw_llm_response    TEXT,
    created_at          TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
);

-- RCA results
CREATE TABLE IF NOT EXISTS rca_results (
    id                  TEXT PRIMARY KEY,
    ticket_id           TEXT NOT NULL,
    root_cause          TEXT,
    recommended_fix     TEXT,
    similar_incident_1  TEXT,                   -- ticket_id of similar case
    similar_incident_2  TEXT,
    similar_incident_3  TEXT,
    similarity_scores   TEXT,                   -- JSON array [0.92, 0.87, 0.81]
    confidence_score    INTEGER,
    risk_tier           TEXT,
    created_at          TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
);

-- Approval workflow actions
CREATE TABLE IF NOT EXISTS approval_actions (
    id                  TEXT PRIMARY KEY,
    ticket_id           TEXT NOT NULL,
    rca_id              TEXT,
    approval_path       TEXT NOT NULL,          -- A / B / C
    action_type         TEXT NOT NULL,          -- APPROVE / REJECT / OVERRIDE / AUTO
    operator_id         TEXT,                   -- 'system' for auto
    operator_reason     TEXT,
    recommended_fix     TEXT,
    confidence_at_time  INTEGER,
    risk_tier           TEXT,
    created_at          TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
);

-- Execution log (what actually ran)
CREATE TABLE IF NOT EXISTS executions (
    id                  TEXT PRIMARY KEY,
    approval_id         TEXT NOT NULL,
    ticket_id           TEXT NOT NULL,
    fix_type            TEXT,                   -- restart_service / clear_cache / scale_up / etc.
    pre_state           TEXT,                   -- JSON snapshot before
    post_state          TEXT,                   -- JSON snapshot after
    outcome             TEXT,                   -- success / failed / rolled_back
    rolled_back         INTEGER DEFAULT 0,
    rollback_reason     TEXT,
    executed_at         TEXT DEFAULT (datetime('now')),
    rolled_back_at      TEXT,
    FOREIGN KEY (approval_id) REFERENCES approval_actions(id)
);

-- Fix outcome history (for trust calibration)
CREATE TABLE IF NOT EXISTS fix_outcomes (
    id                  TEXT PRIMARY KEY,
    category            TEXT NOT NULL,
    fix_type            TEXT NOT NULL,
    approve_count       INTEGER DEFAULT 0,
    reject_count        INTEGER DEFAULT 0,
    rollback_count      INTEGER DEFAULT 0,
    total_actions       INTEGER DEFAULT 0,
    calibrated_confidence INTEGER DEFAULT 50,
    last_updated        TEXT DEFAULT (datetime('now')),
    UNIQUE(category, fix_type)
);

-- Full immutable audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id                  TEXT PRIMARY KEY,
    event_type          TEXT NOT NULL,          -- INGEST/PREDICT/RCA/APPROVE/REJECT/EXECUTE/ROLLBACK/OVERRIDE
    ticket_id           TEXT,
    operator_id         TEXT,
    approval_path       TEXT,
    confidence          INTEGER,
    risk_tier           TEXT,
    action_taken        TEXT,
    reasoning           TEXT,
    outcome             TEXT,
    pre_state           TEXT,
    post_state          TEXT,
    session_hash        TEXT,
    timestamp           TEXT DEFAULT (datetime('now'))
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_tickets_status   ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_severity ON tickets(severity);
CREATE INDEX IF NOT EXISTS idx_audit_ticket     ON audit_log(ticket_id);
CREATE INDEX IF NOT EXISTS idx_audit_type       ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_time       ON audit_log(timestamp);
"""

# ─── Fix type seeds for trust calibration table ──────────────────────────────

FIX_TYPES = [
    ("Database",        "restart_db_service",       45, 3, 1),
    ("Database",        "clear_connection_pool",     38, 4, 2),
    ("Database",        "add_missing_index",         29, 2, 0),
    ("Database",        "increase_pool_size",        22, 1, 0),
    ("Network",         "restart_network_service",   40, 2, 1),
    ("Network",         "update_firewall_rules",     33, 5, 2),
    ("Network",         "failover_to_secondary",     28, 1, 0),
    ("Authentication",  "restart_sso_service",       35, 3, 1),
    ("Authentication",  "rotate_service_account",    44, 2, 0),
    ("Authentication",  "sync_active_directory",     27, 4, 1),
    ("Infrastructure",  "restart_application",       50, 3, 2),
    ("Infrastructure",  "clear_disk_space",          41, 1, 0),
    ("Infrastructure",  "scale_horizontal",          19, 2, 0),
    ("Application",     "rollback_deployment",       31, 1, 0),
    ("Application",     "restart_app_service",       48, 4, 1),
    ("Application",     "update_config",             36, 3, 0),
    ("General", "escalate_to_engineer",  20, 2, 0),
    ("General", "restart_service",       15, 3, 1),
    ("General", "investigate_logs",      25, 1, 0),
]


def seed_fix_outcomes(conn):
    for category, fix_type, approves, rejects, rollbacks in FIX_TYPES:
        total = approves + rejects + rollbacks
        accuracy = approves / total if total > 0 else 0.5
        rollback_penalty = rollbacks * 15
        calibrated = max(0, min(100, int(accuracy * 100 - rollback_penalty)))

        conn.execute("""
            INSERT OR IGNORE INTO fix_outcomes 
            (id, category, fix_type, approve_count, reject_count, rollback_count, 
             total_actions, calibrated_confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), category, fix_type,
              approves, rejects, rollbacks, total, calibrated))


def seed_tickets_from_csv(conn, csv_path):
    """Load normalized tickets into SQLite."""
    inserted = 0
    skipped  = 0

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO tickets 
                    (id, description, severity, category, opened_at, resolved_at,
                     resolution_time_hrs, resolution_notes, assigned_group, resolved_by, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row["id"],
                    row["description"],
                    row["severity"],
                    row["category"],
                    row.get("opened_at", ""),
                    row.get("resolved_at", ""),
                    row.get("resolution_time_hrs") or None,
                    row.get("resolution_notes", ""),
                    row.get("assigned_group", "SUPPORT-TEAM"),
                    row.get("resolved_by", "unknown"),
                    row.get("status", "resolved"),
                ))
                inserted += 1
            except Exception as e:
                skipped += 1
                if skipped <= 3:
                    print(f"   ⚠️  Skipped row: {e}")

    return inserted, skipped


def seed_fake_audit_history(conn, ticket_ids):
    """
    Creates 30 days of fake audit history so dashboard
    doesn't look empty on first run.
    """
    operators = ["rajesh.k", "priya.m", "amit.s", "neha.r", "system"]
    event_types = ["APPROVE", "EXECUTE", "APPROVE", "EXECUTE", "ROLLBACK"]
    outcomes = ["success", "success", "success", "success", "rolled_back"]
    paths = ["A", "B", "A", "B", "C"]

    sample_ids = random.sample(ticket_ids, min(30, len(ticket_ids)))

    for i, tid in enumerate(sample_ids):
        hours_ago = random.randint(1, 720)  # up to 30 days back
        ts = (datetime.now() - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
        event = event_types[i % len(event_types)]
        outcome = outcomes[i % len(outcomes)]

        conn.execute("""
            INSERT INTO audit_log 
            (id, event_type, ticket_id, operator_id, approval_path,
             confidence, risk_tier, action_taken, outcome, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()), event, tid,
            operators[i % len(operators)],
            paths[i % len(paths)],
            random.randint(60, 95),
            random.choice(["Low", "Medium", "Critical"]),
            random.choice(["restart_service", "clear_cache", "update_config"]),
            outcome, ts
        ))


def main():
    parser = argparse.ArgumentParser(description="Setup OpsAI database")
    parser.add_argument("--data",   default="data/tickets_clean.csv", help="Path to clean CSV")
    parser.add_argument("--db",     default=DB_PATH,                  help="SQLite DB path")
    parser.add_argument("--reset",  action="store_true",              help="Drop and recreate DB")
    args = parser.parse_args()

    import os
    os.makedirs("db", exist_ok=True)

    if args.reset and os.path.exists(args.db):
        os.remove(args.db)
        print("🗑️  Dropped existing database")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    print(f"\n🏗️  Creating schema...")
    conn.executescript(SCHEMA)
    conn.commit()
    print("   ✅ All tables created")

    print(f"\n🌱 Seeding fix outcome history (trust calibration)...")
    seed_fix_outcomes(conn)
    conn.commit()
    print(f"   ✅ {len(FIX_TYPES)} fix types seeded")

    print(f"\n📥 Loading tickets from {args.data}...")
    inserted, skipped = seed_tickets_from_csv(conn, args.data)
    conn.commit()
    print(f"   ✅ {inserted} tickets inserted, {skipped} skipped")

    # Get IDs of resolved tickets for audit seeding
    rows = conn.execute(
        "SELECT id FROM tickets WHERE status='resolved' LIMIT 500"
    ).fetchall()
    ticket_ids = [r["id"] for r in rows]

    print(f"\n📋 Seeding fake audit history (30 days)...")
    seed_fake_audit_history(conn, ticket_ids)
    conn.commit()
    print(f"   ✅ {min(30, len(ticket_ids))} audit entries created")

    # Final summary
    stats = {
        "tickets":          conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0],
        "resolved":         conn.execute("SELECT COUNT(*) FROM tickets WHERE status='resolved'").fetchone()[0],
        "audit_entries":    conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
        "fix_types":        conn.execute("SELECT COUNT(*) FROM fix_outcomes").fetchone()[0],
    }

    print(f"\n{'='*45}")
    print(f"✅ Database ready: {args.db}")
    print(f"{'='*45}")
    for key, val in stats.items():
        print(f"   {key:<20} {val}")

    conn.close()


if __name__ == "__main__":
    main()
