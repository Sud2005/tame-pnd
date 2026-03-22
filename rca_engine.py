"""
Phase 3 — Root Cause Analysis Engine
======================================
Uses sentence-transformers + FAISS to find the 3 most similar
resolved incidents, then synthesizes root cause + fix via Groq.

Built for ITSM_data.csv columns:
  CI_Name, CI_Cat, CI_Subcat, Priority, Closure_Code,
  Handle_Time_hrs, No_of_Reassignments, Alert_Status

Architecture:
  1. On server startup → embed all resolved tickets → FAISS index
  2. On RCA request   → embed new ticket → search index → top-3 matches
  3. Build rich prompt with matches → Groq synthesizes root cause
  4. Write result to rca_results table + audit log

Run standalone test:
    python rca_engine.py
"""

import json
import os
import pickle
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

load_dotenv()

DB_PATH         = "db/opsai.db"
INDEX_PATH      = "db/faiss.index"       # saved FAISS index
STORE_PATH      = "db/memory_store.pkl"  # parallel list of ticket dicts
EMBED_MODEL     = "all-MiniLM-L6-v2"    # fast, accurate, free, runs locally
GROQ_MODEL      = "llama-3.3-70b-versatile"
MIN_RESOLVED    = 5    # minimum resolved tickets needed before RCA is useful

# Lazy-loaded globals (only built once at startup)
_faiss_index    = None
_memory_store   = None   # list of dicts, parallel to FAISS index
_embed_model    = None


# ── Embedding model ───────────────────────────────────────────────────────────

def get_embed_model():
    global _embed_model
    if _embed_model is None:
        print("📥 Loading sentence-transformer model (first time only)...")
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer(EMBED_MODEL)
        print(f"   ✅ Model loaded: {EMBED_MODEL}")
    return _embed_model


def embed_text(text: str) -> np.ndarray:
    """Embed a single string. Returns shape (384,) float32 array."""
    model  = get_embed_model()
    vector = model.encode(text, convert_to_numpy=True, show_progress_bar=False)
    return vector.astype(np.float32)


def embed_batch(texts: list[str], batch_size: int = 256) -> np.ndarray:
    """Embed a list of strings efficiently. Returns shape (N, 384)."""
    model   = get_embed_model()
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        convert_to_numpy=True,
        show_progress_bar=True,
        normalize_embeddings=True,   # L2 normalize for cosine similarity via dot product
    )
    return vectors.astype(np.float32)


# ── Build the text to embed for each ticket ──────────────────────────────────

def build_ticket_text(ticket: dict) -> str:
    """
    Combines all meaningful fields into one string for embedding.
    Better context = better similarity search.
    
    Uses actual ITSM_data.csv fields where available.
    """
    parts = []

    # Description / CI_Name
    desc = ticket.get("description", "")
    if desc and desc.lower() not in ("no description provided", "nan", ""):
        parts.append(desc)

    # Category and subcategory (CI_Cat, CI_Subcat)
    ci_cat    = ticket.get("ci_cat", ticket.get("CI_Cat", ""))
    ci_subcat = ticket.get("ci_subcat", ticket.get("CI_Subcat", ""))
    if ci_cat:    parts.append(f"Component type: {ci_cat}")
    if ci_subcat: parts.append(f"Component: {ci_subcat}")

    # Category
    cat = ticket.get("category", "")
    if cat and cat.lower() not in ("general", "incident", ""):
        parts.append(f"Category: {cat}")

    # Severity
    sev = ticket.get("severity", "")
    if sev: parts.append(f"Severity: {sev}")

    # Resolution notes / Closure_Code (the gold: what fixed it)
    resolution = (
        ticket.get("resolution_notes") or
        ticket.get("closure_code") or
        ticket.get("Closure_Code") or ""
    )
    if resolution and resolution.lower() not in ("nan","none",""):
        parts.append(f"Resolution: {resolution}")

    return " | ".join(parts) if parts else desc or "unknown incident"


# ── FAISS index management ────────────────────────────────────────────────────

def build_index(force_rebuild: bool = False) -> tuple:
    """
    Builds FAISS index from all resolved tickets in DB.
    Saves index + memory store to disk.
    Skips rebuild if saved files already exist (unless force_rebuild=True).

    Returns: (faiss_index, memory_store)
    """
    import faiss

    if not force_rebuild and Path(INDEX_PATH).exists() and Path(STORE_PATH).exists():
        print("📂 Loading existing FAISS index from disk...")
        index  = faiss.read_index(INDEX_PATH)
        with open(STORE_PATH, "rb") as f:
            store = pickle.load(f)
        print(f"   ✅ Index loaded: {index.ntotal:,} vectors")
        return index, store

    print("🔨 Building FAISS index...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Attempt 1: resolved tickets WITH resolution notes (ideal for RCA quality)
    rows = conn.execute("""
        SELECT id, description, severity, category, resolution_notes,
               resolution_time_hrs, status
        FROM   tickets
        WHERE  status = 'resolved'
          AND  resolution_notes IS NOT NULL
          AND  resolution_notes NOT IN ('', 'nan', 'None', 'NaN')
        ORDER  BY opened_at DESC
        LIMIT  50000
    """).fetchall()
    print(f"   Resolved with notes: {len(rows):,}")

    # Attempt 2: ITSM_data.csv has sparse Closure_Code — use all resolved
    if len(rows) < MIN_RESOLVED:
        print(f"   ⚠️  Too few with notes. Expanding to all resolved tickets...")
        rows = conn.execute("""
            SELECT id, description, severity, category, resolution_notes,
                   resolution_time_hrs, status
            FROM   tickets
            WHERE  status = 'resolved'
            ORDER  BY opened_at DESC
            LIMIT  50000
        """).fetchall()
        print(f"   All resolved: {len(rows):,}")

    # Attempt 3: last resort — include all tickets regardless of status
    if len(rows) < MIN_RESOLVED:
        print(f"   ⚠️  Not enough resolved. Including all tickets...")
        rows = conn.execute("""
            SELECT id, description, severity, category, resolution_notes,
                   resolution_time_hrs, status
            FROM   tickets
            ORDER  BY opened_at DESC
            LIMIT  50000
        """).fetchall()
        print(f"   All tickets: {len(rows):,}")

    conn.close()

    if len(rows) < MIN_RESOLVED:
        raise RuntimeError(
            f"Only {len(rows)} tickets in database. "
            f"Run: python setup_db.py --data data/tickets_clean.csv"
        )

    tickets = [dict(r) for r in rows]
    texts   = [build_ticket_text(t) for t in tickets]

    print(f"   Embedding {len(tickets):,} resolved tickets...")
    vectors = embed_batch(texts)

    # IndexFlatIP = inner product (cosine similarity after L2 normalization)
    # For large datasets (>100k) swap to IndexIVFFlat for faster search
    dim   = vectors.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(vectors)

    # Save to disk
    os.makedirs("db", exist_ok=True)
    faiss.write_index(index, INDEX_PATH)
    with open(STORE_PATH, "wb") as f:
        pickle.dump(tickets, f)

    print(f"   ✅ Index built: {index.ntotal:,} vectors (dim={dim})")
    print(f"   💾 Saved to {INDEX_PATH}")
    return index, tickets


def get_index():
    """Returns the global index, building it if not loaded yet."""
    global _faiss_index, _memory_store
    if _faiss_index is None or _memory_store is None:
        _faiss_index, _memory_store = build_index()
    return _faiss_index, _memory_store


def add_to_index(ticket: dict):
    """
    Adds a newly resolved ticket to the in-memory index.
    Also persists the updated store to disk.
    This is how the system learns in real time.
    """
    global _faiss_index, _memory_store
    import faiss

    if _faiss_index is None:
        return  # Index not loaded yet, will be rebuilt on next startup

    text   = build_ticket_text(ticket)
    vector = embed_text(text).reshape(1, -1)

    _faiss_index.add(vector)
    _memory_store.append(ticket)

    # Persist updated store (re-save index + store)
    faiss.write_index(_faiss_index, INDEX_PATH)
    with open(STORE_PATH, "wb") as f:
        pickle.dump(_memory_store, f)


# ── Semantic search ───────────────────────────────────────────────────────────

def search_similar(description: str, k: int = 3) -> list[dict]:
    """
    Finds k most similar resolved incidents to the given description.
    Returns list of dicts with ticket data + similarity_score.
    """
    index, store = get_index()

    query_vec = embed_text(description).reshape(1, -1)
    scores, indices = index.search(query_vec, k=min(k, index.ntotal))

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx == -1:   # FAISS returns -1 for empty slots
            continue
        ticket = store[idx].copy()
        ticket["similarity_score"] = round(float(score), 4)
        ticket["similarity_pct"]   = round(float(score) * 100, 1)
        results.append(ticket)

    return results


# ── Groq RCA synthesis prompt ─────────────────────────────────────────────────

def build_rca_prompt(new_ticket: dict, similar: list[dict]) -> str:
    """
    Builds the prompt that asks Groq to synthesize root cause
    from the 3 most similar past incidents.
    """
    # Format each similar incident
    past_incidents = ""
    for i, t in enumerate(similar, 1):
        resolution = (
            t.get("resolution_notes") or
            t.get("closure_code") or
            "Resolution not recorded"
        )
        past_incidents += f"""
Past Incident #{i} (Similarity: {t.get('similarity_pct', '?')}%)
  Incident:   {t.get('description', 'N/A')[:120]}
  Category:   {t.get('category', 'N/A')}
  Severity:   {t.get('severity', 'N/A')}
  Resolution: {resolution[:200]}
  MTTR:       {t.get('resolution_time_hrs', 'unknown')} hours
"""

    new_desc     = new_ticket.get("description", "")
    new_category = new_ticket.get("category", "General")
    new_severity = new_ticket.get("severity", "P2")
    new_ci_cat   = new_ticket.get("ci_cat", "")
    new_ci_sub   = new_ticket.get("ci_subcat", "")

    context = f"Category: {new_category}"
    if new_ci_cat:   context += f" | CI Type: {new_ci_cat}"
    if new_ci_sub:   context += f" | Component: {new_ci_sub}"
    context += f" | Severity: {new_severity}"

    return f"""You are a senior IT operations engineer with 15 years of experience.
You are performing Root Cause Analysis on a new incident.

You have access to 3 similar resolved incidents from the past as reference.

─── NEW INCIDENT ───────────────────────────────────────────────
Description: {new_desc}
Context:     {context}

─── SIMILAR PAST INCIDENTS (retrieved by semantic similarity) ──
{past_incidents}

Based on the patterns in these past incidents, perform a thorough RCA.

Respond ONLY with valid JSON. No markdown. No explanation outside the JSON.

{{
  "root_cause": "Clear explanation of the most likely root cause, 1-2 sentences, grounded in the past incident patterns",
  "confidence_score": integer 0-100,
  "risk_tier": "Low or Medium or Critical",
  "recommended_fix": "The single most important first action to take right now",
  "fix_steps": ["step 1", "step 2", "step 3"],
  "estimated_resolution_hrs": number,
  "pattern_match": "What common pattern across the past incidents led to this diagnosis",
  "source_citations": ["Past Incident #1: brief note", "Past Incident #2: brief note"],
  "warnings": "Any risks or caveats the operator should know before executing the fix, or null"
}}"""


# ── Main RCA function — call from ingestion.py ────────────────────────────────

def run_rca(ticket_id: str) -> dict:
    """
    Full RCA pipeline for one ticket.

    1. Load ticket from DB
    2. Search FAISS for 3 similar resolved incidents
    3. Build prompt + call Groq
    4. Parse response
    5. Write to rca_results table + audit log
    6. Return full result

    Args:
        ticket_id: ID of the ticket to analyse

    Returns:
        dict with full RCA result including approval_path
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Load ticket
    ticket_row = conn.execute(
        "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()

    if not ticket_row:
        conn.close()
        raise ValueError(f"Ticket {ticket_id} not found in database")

    ticket  = dict(ticket_row)
    rca_id  = str(uuid.uuid4())
    now     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Load prediction context if available (enriches RCA)
    pred_row = conn.execute(
        "SELECT * FROM predictions WHERE ticket_id = ? ORDER BY created_at DESC LIMIT 1",
        (ticket_id,)
    ).fetchone()
    if pred_row:
        pred = dict(pred_row)
        ticket["ci_cat"]   = ticket.get("ci_cat", pred.get("predicted_category",""))
        ticket["category"] = ticket.get("category", pred.get("predicted_category",""))

    result = None

    try:
        # ── Step 1: FAISS semantic search ─────────────────────────────────────
        description = ticket.get("description","")
        similar     = search_similar(description, k=3)

        if not similar:
            raise RuntimeError("No similar incidents found in index")

        # ── Step 2: Groq synthesis ────────────────────────────────────────────
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise EnvironmentError("GROQ_API_KEY not set")

        from groq import Groq
        client = Groq(api_key=api_key)
        prompt = build_rca_prompt(ticket, similar)

        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert IT operations engineer performing root cause analysis. "
                        "Always respond with valid JSON only. No markdown. No extra text."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,     # Slightly higher than prediction — we want thoughtful analysis
            max_tokens=600,
        )

        raw = response.choices[0].message.content

        # ── Step 3: Parse response ────────────────────────────────────────────
        parsed = _parse_rca_response(raw)

        # ── Step 4: Determine approval path from RCA confidence ───────────────
        # RCA result can change the approval path if it discovers higher risk
        sev    = ticket.get("severity","P3")
        conf   = parsed["confidence_score"]
        risk   = parsed["risk_tier"]
        path   = _get_approval_path(conf, risk, sev)

        result = {
            "id":                       rca_id,
            "ticket_id":                ticket_id,
            "root_cause":               parsed["root_cause"],
            "confidence_score":         parsed["confidence_score"],
            "risk_tier":                parsed["risk_tier"],
            "recommended_fix":          parsed["recommended_fix"],
            "fix_steps":                parsed["fix_steps"],
            "estimated_resolution_hrs": parsed["estimated_resolution_hrs"],
            "pattern_match":            parsed["pattern_match"],
            "source_citations":         parsed["source_citations"],
            "warnings":                 parsed.get("warnings"),
            "approval_path":            path,
            "similar_incidents":        [
                {
                    "id":               s.get("id",""),
                    "description":      s.get("description","")[:100],
                    "resolution":       (s.get("resolution_notes") or s.get("closure_code",""))[:150],
                    "similarity_score": s.get("similarity_score", 0),
                    "similarity_pct":   s.get("similarity_pct", 0),
                    "severity":         s.get("severity",""),
                    "category":         s.get("category",""),
                    "mttr_hrs":         s.get("resolution_time_hrs",""),
                }
                for s in similar
            ],
            "model_used":   GROQ_MODEL,
            "status":       "success",
        }

    except Exception as e:
        print(f"⚠️  RCA failed for {ticket_id}: {e}")
        result = _fallback_rca(ticket_id, rca_id, str(e))

    # ── Step 5: Persist to DB ─────────────────────────────────────────────────
    try:
        sim = result.get("similar_incidents", [])
        conn.execute("""
            INSERT INTO rca_results
            (id, ticket_id, root_cause, recommended_fix,
             similar_incident_1, similar_incident_2, similar_incident_3,
             similarity_scores, confidence_score, risk_tier, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            result["id"], result["ticket_id"],
            result["root_cause"], result["recommended_fix"],
            sim[0]["id"] if len(sim) > 0 else None,
            sim[1]["id"] if len(sim) > 1 else None,
            sim[2]["id"] if len(sim) > 2 else None,
            json.dumps([s.get("similarity_score",0) for s in sim]),
            result["confidence_score"],
            result["risk_tier"],
            now,
        ))

        # Update ticket status to pending_approval
        conn.execute(
            "UPDATE tickets SET status='pending_approval' WHERE id=?",
            (ticket_id,)
        )

        # Audit log
        conn.execute("""
            INSERT INTO audit_log
            (id, event_type, ticket_id, confidence, risk_tier,
             action_taken, reasoning, outcome, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            str(uuid.uuid4()), "RCA", ticket_id,
            result["confidence_score"], result["risk_tier"],
            f"RCA complete → Approval Path {result['approval_path']}",
            result["root_cause"][:200],
            result["status"], now,
        ))

        conn.commit()
    except Exception as e:
        print(f"⚠️  RCA DB write failed: {e}")
    finally:
        conn.close()

    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_rca_response(raw: str) -> dict:
    text = raw.strip()
    if "```" in text:
        parts = text.split("```")
        text  = parts[1] if len(parts) > 1 else parts[0]
        if text.startswith("json"): text = text[4:]
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1: raise ValueError(f"No JSON in RCA response: {raw[:150]}")

    p = json.loads(text[start:end])

    # Normalize with safe defaults
    p["root_cause"]               = str(p.get("root_cause","Unknown root cause"))
    p["confidence_score"]         = max(0, min(100, int(p.get("confidence_score", 60))))
    p["risk_tier"]                = str(p.get("risk_tier","Medium")).strip().title()
    if p["risk_tier"] not in ("Low","Medium","Critical"): p["risk_tier"] = "Medium"
    p["recommended_fix"]          = str(p.get("recommended_fix","Escalate to senior engineer"))
    p["fix_steps"]                = p.get("fix_steps", [])
    if not isinstance(p["fix_steps"], list): p["fix_steps"] = [str(p["fix_steps"])]
    p["estimated_resolution_hrs"] = float(p.get("estimated_resolution_hrs", 2.0))
    p["pattern_match"]            = str(p.get("pattern_match",""))
    p["source_citations"]         = p.get("source_citations", [])
    if not isinstance(p["source_citations"], list): p["source_citations"] = []

    return p


def _get_approval_path(confidence: int, risk_tier: str, severity: str) -> str:
    if severity == "P1" or risk_tier == "Critical": return "C"
    if risk_tier == "Medium" or confidence < 85:    return "B"
    return "A"


def _fallback_rca(ticket_id: str, rca_id: str, error: str) -> dict:
    return {
        "id":                       rca_id,
        "ticket_id":                ticket_id,
        "root_cause":               "RCA unavailable — manual investigation required",
        "confidence_score":         0,
        "risk_tier":                "Critical",
        "recommended_fix":          "Escalate to senior engineer immediately",
        "fix_steps":                ["Escalate to on-call engineer", "Gather logs", "Investigate manually"],
        "estimated_resolution_hrs": 4.0,
        "pattern_match":            "No pattern match available",
        "source_citations":         [],
        "warnings":                 f"RCA engine error: {error[:100]}",
        "approval_path":            "C",
        "similar_incidents":        [],
        "model_used":               "fallback",
        "status":                   "fallback",
    }


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🧪 Phase 3 — RCA Engine Test")
    print("=" * 60)

    # Step 1: Build index
    print("\n[1] Building FAISS index...")
    try:
        index, store = build_index(force_rebuild=True)
        print(f"    ✅ Index ready — {index.ntotal:,} vectors")
    except Exception as e:
        print(f"    ❌ {e}")
        exit(1)

    # Step 2: Test semantic search
    print("\n[2] Semantic Search Test")
    test_queries = [
        "Database connection pool exhausted, application throwing errors",
        "VPN gateway unresponsive, remote workers cannot connect",
        "SSL certificate expired on production load balancer",
    ]
    for q in test_queries:
        results = search_similar(q, k=3)
        print(f"\n    Query: \"{q[:55]}...\"")
        for r in results:
            print(f"      {r['similarity_pct']:5.1f}%  [{r['severity']}] {r['description'][:60]}")

    # Step 3: Full RCA on a real ticket from DB
    print("\n[3] Full RCA Pipeline Test")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ticket = conn.execute(
        "SELECT id FROM tickets WHERE status='open' LIMIT 1"
    ).fetchone()
    conn.close()

    if ticket:
        tid = ticket["id"]
        print(f"    Running RCA on ticket: {tid}")
        try:
            result = run_rca(tid)
            print(f"\n    ✅ RCA Complete")
            print(f"    {'Root Cause':<25} {result['root_cause'][:65]}")
            print(f"    {'Confidence':<25} {result['confidence_score']}%")
            print(f"    {'Risk Tier':<25} {result['risk_tier']}")
            print(f"    {'Recommended Fix':<25} {result['recommended_fix'][:65]}")
            print(f"    {'Fix Steps':<25} {len(result['fix_steps'])} steps")
            print(f"    {'Est. Resolution':<25} {result['estimated_resolution_hrs']} hrs")
            print(f"    {'Approval Path':<25} {result['approval_path']}")
            print(f"    {'Similar Incidents':<25} {len(result['similar_incidents'])} found")
            print(f"    {'Pattern Match':<25} {result['pattern_match'][:65]}")
            if result.get("warnings"):
                print(f"    {'Warnings':<25} {result['warnings'][:65]}")
            print(f"\n    Source Citations:")
            for c in result["source_citations"]:
                print(f"      — {c}")
        except Exception as e:
            print(f"    ❌ RCA failed: {e}")
    else:
        print("    ⚠️  No open tickets found. Ingest some tickets first.")

    print("\n" + "=" * 60)
    print("✅ Phase 3 test complete")
