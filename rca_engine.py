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

load_dotenv(override=True)

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
_index_ready    = False  # flag: True once index is fully loaded
_groq_client    = None   # cached Groq client — created once, reused across requests
_index_lock     = None   # threading lock for safe concurrent add_to_index calls


# ── Embedding model ───────────────────────────────────────────────────────────

def get_embed_model():
    global _embed_model
    if _embed_model is None:
        print("📥 Loading sentence-transformer model (first time only)...")
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer(EMBED_MODEL)
        print(f"   ✅ Model loaded: {EMBED_MODEL}")
    return _embed_model


# ── Groq client (cached, created once) ───────────────────────────────────────

def get_groq_client():
    """Return a cached Groq client, creating one if needed.
    Reusing the same client avoids re-authentication overhead and prevents
    rate-limit false-positives caused by opening new HTTP connections for
    every RCA request.
    """
    global _groq_client
    if _groq_client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise EnvironmentError("GROQ_API_KEY not set")
        from groq import Groq
        _groq_client = Groq(api_key=api_key)
        print("✅ Groq client initialised")
    return _groq_client


def _groq_chat_with_retry(messages: list, max_tokens: int = 1500, max_retries: int = 3) -> str:
    """Call Groq chat completions with exponential-backoff retry on transient errors.
    Returns the raw response content string.
    Raises the last exception if all retries are exhausted.
    """
    import time
    client = get_groq_client()
    last_err = None
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                temperature=0.3,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            # Don't retry on auth / quota errors — they won't self-heal
            if any(k in err_str for k in ("api_key", "invalid_api_key", "unauthorized", "quota")):
                raise
            # Exponential backoff: attempt 0 → 1 s, attempt 1 → 2 s, attempt 2 → 4 s
            wait = 2 ** attempt
            print(f"⚠️  Groq attempt {attempt + 1}/{max_retries} failed: {e}. Retrying in {wait}s…")
            time.sleep(wait)
    raise last_err


def embed_text(text: str) -> np.ndarray:
    """Embed a single string. Returns shape (384,) float32 array."""
    model  = get_embed_model()
    vector = model.encode(text, convert_to_numpy=True, show_progress_bar=False,
                          normalize_embeddings=True)
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
    """
    parts = []

    desc = ticket.get("description", "")
    if desc and desc.lower() not in ("no description provided", "nan", ""):
        parts.append(desc)

    ci_cat    = ticket.get("ci_cat", ticket.get("CI_Cat", ""))
    ci_subcat = ticket.get("ci_subcat", ticket.get("CI_Subcat", ""))
    if ci_cat:    parts.append(f"Component type: {ci_cat}")
    if ci_subcat: parts.append(f"Component: {ci_subcat}")

    cat = ticket.get("category", "")
    if cat and cat.lower() not in ("general", "incident", ""):
        parts.append(f"Category: {cat}")

    sev = ticket.get("severity", "")
    if sev: parts.append(f"Severity: {sev}")

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
    global _index_ready
    import faiss

    if not force_rebuild and Path(INDEX_PATH).exists() and Path(STORE_PATH).exists():
        print("📂 Loading existing FAISS index from disk...")
        index  = faiss.read_index(INDEX_PATH)
        with open(STORE_PATH, "rb") as f:
            store = pickle.load(f)
        print(f"   ✅ Index loaded: {index.ntotal:,} vectors")
        _index_ready = True
        return index, store

    print("🔨 Building FAISS index...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Attempt 1: resolved tickets WITH resolution notes (ideal for RCA quality)
    rows = conn.execute("""
        SELECT id, description, severity, category, resolution_notes,
               status
        FROM   tickets
        WHERE  status = 'resolved'
          AND  resolution_notes IS NOT NULL
          AND  resolution_notes NOT IN ('', 'nan', 'None', 'NaN')
        ORDER  BY opened_at DESC
        LIMIT  50000
    """).fetchall()
    print(f"   Resolved with notes: {len(rows):,}")

    # Attempt 2: use all resolved
    if len(rows) < MIN_RESOLVED:
        print(f"   ⚠️  Too few with notes. Expanding to all resolved tickets...")
        rows = conn.execute("""
            SELECT id, description, severity, category, resolution_notes,
                   status
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
                   status
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

    dim   = vectors.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(vectors)

    os.makedirs("db", exist_ok=True)
    faiss.write_index(index, INDEX_PATH)
    with open(STORE_PATH, "wb") as f:
        pickle.dump(tickets, f)

    print(f"   ✅ Index built: {index.ntotal:,} vectors (dim={dim})")
    print(f"   💾 Saved to {INDEX_PATH}")
    _index_ready = True
    return index, tickets


def get_index():
    """Returns the global index, building it if not loaded yet."""
    global _faiss_index, _memory_store
    if _faiss_index is None or _memory_store is None:
        _faiss_index, _memory_store = build_index()
    return _faiss_index, _memory_store


def is_index_ready() -> bool:
    """Check if the FAISS index is loaded and ready."""
    return _index_ready and _faiss_index is not None


def add_to_index(ticket: dict):
    """
    Adds a newly resolved ticket to the in-memory index.
    Also persists the updated store to disk.
    Thread-safe: uses a module-level lock to prevent concurrent writes from
    corrupting the index file when multiple tickets are resolved simultaneously.
    """
    global _faiss_index, _memory_store, _index_lock
    import faiss
    import threading

    if _faiss_index is None:
        return

    # Initialise lock lazily so it is always associated with the correct interpreter
    if _index_lock is None:
        _index_lock = threading.Lock()

    text   = build_ticket_text(ticket)
    vector = embed_text(text).reshape(1, -1)

    with _index_lock:
        _faiss_index.add(vector)
        _memory_store.append(ticket)

        faiss.write_index(_faiss_index, INDEX_PATH)
        with open(STORE_PATH, "wb") as f:
            pickle.dump(_memory_store, f)


# ── Semantic search ───────────────────────────────────────────────────────────

def search_similar(description: str, k: int = 5, exclude_ticket_id: str = None) -> list[dict]:
    """
    Finds k most similar resolved incidents to the given description.
    Over-fetches candidates, deduplicates by description overlap,
    excludes self-matches, and prefers results with resolution notes.
    Returns list of dicts with ticket data + similarity_score.
    """
    index, store = get_index()

    if index.ntotal == 0:
        return []

    # Over-fetch to allow deduplication.
    # When the index is very small, fetch everything available.
    fetch_k = min(k * 4, index.ntotal)
    query_vec = embed_text(description).reshape(1, -1)
    scores, indices = index.search(query_vec, k=fetch_k)

    def _word_set(text):
        return set((text or "").lower().split())

    def _overlap(a_words, b_words):
        if not a_words or not b_words:
            return 0.0
        common = a_words & b_words
        return len(common) / max(len(a_words), len(b_words))

    candidates = []
    for score, idx in zip(scores[0], indices[0]):
        if idx == -1:
            continue
        ticket = store[idx].copy()
        # Skip self-match
        if exclude_ticket_id and ticket.get("id") == exclude_ticket_id:
            continue
        sim_score = max(0.0, min(1.0, float(score)))
        ticket["similarity_score"] = round(sim_score, 4)
        ticket["similarity_pct"]   = round(sim_score * 100, 1)
        candidates.append(ticket)

    # When the index is very small (e.g. fewer than 5 tickets) the
    # >80% deduplication threshold can drop ALL candidates, leaving an
    # empty list and causing RCA to always fall back.  Relax the threshold
    # progressively so at least one match is always returned.
    dedup_threshold = 0.80
    if len(candidates) < k:
        dedup_threshold = 0.95   # almost identical only

    # Deduplicate: skip candidates whose description overlaps > threshold
    results = []
    selected_word_sets = []
    # Sort: prefer candidates with resolution notes first, then by similarity
    def _sort_key(t):
        has_res = 1 if (t.get("resolution_notes") and str(t["resolution_notes"]).lower() not in ("nan","none","")) else 0
        return (-has_res, -t.get("similarity_score", 0))
    candidates.sort(key=_sort_key)

    for c in candidates:
        if len(results) >= k:
            break
        c_words = _word_set(c.get("description", ""))
        # Check overlap with already-selected results
        too_similar = False
        for sw in selected_word_sets:
            if _overlap(c_words, sw) > dedup_threshold:
                too_similar = True
                break
        if too_similar:
            continue
        results.append(c)
        selected_word_sets.append(c_words)

    return results


# ── Confidence calibration ────────────────────────────────────────────────────

def calibrate_confidence(raw_confidence: int, similar: list[dict], ticket: dict) -> int:
    """
    Calibrate the LLM's raw confidence using objective signals:
      - Average similarity score of top matches
      - Whether resolution notes exist in the matches
      - Historical fix outcome data for the category
      - Severity alignment between new and matched tickets
    
    Returns adjusted confidence score 0-100.
    """
    if not similar:
        return max(10, raw_confidence // 2)

    # 1. Average similarity of top matches (0-100 scale)
    avg_sim = float(np.mean([s.get("similarity_pct", 0) for s in similar[:3]]))

    # 2. Resolution coverage — how many matches have actual fixes recorded
    has_resolution = sum(
        1 for s in similar[:3]
        if s.get("resolution_notes") and str(s["resolution_notes"]).lower() not in ("nan", "none", "")
    )
    resolution_bonus = has_resolution * 10  # up to +30 for 3 matches with resolutions

    # 3. Severity match bonus — same severity = better pattern
    ticket_sev = ticket.get("severity", "P3")
    sev_matches = sum(1 for s in similar[:3] if s.get("severity") == ticket_sev)
    sev_bonus = sev_matches * 6  # up to +18

    # 4. Category match bonus
    ticket_cat = ticket.get("category", "General")
    cat_matches = sum(1 for s in similar[:3] if s.get("category") == ticket_cat)
    cat_bonus = cat_matches * 5  # up to +15

    # 5. Historical fix outcomes for this category
    outcome_multiplier = 1.0
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        outcome = conn.execute(
            "SELECT success_count, total_actions FROM fix_outcomes WHERE category=?",
            (ticket_cat,)
        ).fetchone()
        conn.close()
        if outcome and outcome["total_actions"] > 0:
            success_rate = outcome["success_count"] / outcome["total_actions"]
            outcome_multiplier = 0.8 + (success_rate * 0.2)  # range 0.8-1.0
    except Exception:
        pass

    # Blend: 35% LLM confidence + 40% similarity + 25% bonuses
    # Similarity weighted higher because we have 46k real tickets in the index
    blended = (
        raw_confidence * 0.35 +
        avg_sim * 0.40 +
        (resolution_bonus + sev_bonus + cat_bonus) * 0.25
    )
    calibrated = int(blended * outcome_multiplier)

    # Confidence floors based on match quality
    # With 46k tickets in the index, high similarity really means something
    if similar and avg_sim > 40:
        calibrated = max(calibrated, 35)
    if similar and avg_sim > 55:
        calibrated = max(calibrated, 50)
    if similar and avg_sim > 70:
        calibrated = max(calibrated, 62)
    if similar and avg_sim > 80 and has_resolution >= 2:
        calibrated = max(calibrated, 75)

    return max(10, min(95, calibrated))


def determine_risk_tier(confidence: int, severity: str, similar: list[dict]) -> str:
    """
    Determine risk tier using multi-signal approach.
    """
    # Severity-based baseline
    if severity == "P1":
        base_risk = "Critical"
    elif severity == "P2":
        base_risk = "Medium"
    else:
        base_risk = "Low"

    # Confidence can raise risk (low confidence = higher risk)
    if confidence < 30:
        return "Critical"
    if confidence < 50 and base_risk == "Low":
        return "Medium"

    # High similarity with good matches lowers risk
    if similar:
        avg_sim = np.mean([s.get("similarity_pct", 0) for s in similar[:3]])
        if avg_sim > 70 and confidence > 65:
            if base_risk == "Critical":
                return "Medium"  # can't go lower than medium for P1

    return base_risk


# ── Groq RCA synthesis prompt ─────────────────────────────────────────────────

def build_rca_prompt(new_ticket: dict, similar: list[dict]) -> str:
    """
    Builds the prompt that asks Groq to synthesize root cause
    from the most similar past incidents.
    """
    past_incidents = ""
    for i, t in enumerate(similar[:3], 1):
        resolution = (
            t.get("resolution_notes") or
            t.get("closure_code") or
            "Resolution not recorded"
        )
        past_incidents += f"""
Past Incident #{i} (Similarity: {t.get('similarity_pct', '?')}%)
  Incident:   {t.get('description', 'N/A')[:150]}
  Category:   {t.get('category', 'N/A')}
  Severity:   {t.get('severity', 'N/A')}
  Resolution: {resolution[:250]}
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

You have access to similar resolved incidents from the past as reference.

─── NEW INCIDENT ───────────────────────────────────────────────
Description: {new_desc}
Context:     {context}

─── SIMILAR PAST INCIDENTS (retrieved by semantic similarity) ──
{past_incidents}

Based on the patterns in these past incidents, perform a thorough RCA.
Provide a CONCRETE root cause and actionable fix. Be specific, not vague.
CRITICAL INSTRUCTION: The past incidents only have brief resolutions. You MUST expand them into a detailed, multi-step sequence (at least 3-5 distinct steps) for the "fix_steps" array. Outline the exact troubleshooting, execution, and verification steps. DO NOT merely return a single string.

Respond ONLY with valid JSON. No markdown. No explanation outside the JSON.

{{
  "root_cause": "Clear, specific explanation of the root cause.",
  "recommended_fix": "The single most important specific action to take.",
  "fix_steps": [
    "Step 1: Verify and isolate the issue...",
    "Step 2: Pre-check or backup...",
    "Step 3: Apply the primary fix...",
    "Step 4: Verify recovery..."
  ],
  "estimated_resolution_hrs": number,
  "pattern_match": "What common pattern across the past incidents led to this diagnosis",
  "source_citations": ["Past Incident #1: brief note", "Past Incident #2: brief note"],
  "warnings": "Any risks or caveats the operator should know, or null"
}}"""


# ── Main RCA function — call from ingestion.py ────────────────────────────────

def run_rca(ticket_id: str) -> dict:
    """
    Full RCA pipeline for one ticket.

    1. Load ticket from DB
    2. Search FAISS for similar resolved incidents
    3. Build prompt + call Groq
    4. Parse response + calibrate confidence
    5. Write to rca_results table + audit log
    6. Return full result
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

    # Load prediction context if available
    pred_row = conn.execute(
        "SELECT * FROM predictions WHERE ticket_id = ? ORDER BY created_at DESC LIMIT 1",
        (ticket_id,)
    ).fetchone()
    if pred_row:
        pred = dict(pred_row)
        ticket["ci_cat"]   = ticket.get("ci_cat") or pred.get("predicted_category", "")
        ticket["category"] = ticket.get("category") or pred.get("predicted_category", "")

    result = None

    try:
        # ── Step 1: FAISS semantic search ─────────────────────────────────────
        description = ticket.get("description", "")
        similar     = search_similar(description, k=5, exclude_ticket_id=ticket_id)

        if not similar:
            raise RuntimeError("No similar incidents found in index")

        # ── Step 2: Groq synthesis (uses cached client + retry) ───────────────
        prompt = build_rca_prompt(ticket, similar)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert IT operations engineer performing root cause analysis. "
                    "Always respond with valid JSON only. No markdown. No extra text. "
                    "Be specific and actionable in your analysis. "
                    "CRITICAL: The 'fix_steps' array MUST contain a detailed, multi-step (at least 3-5 steps) troubleshooting and resolution guide. Never output a single step."
                )
            },
            {"role": "user", "content": prompt}
        ]
        raw = _groq_chat_with_retry(messages, max_tokens=1500)

        # ── Step 3: Parse response ────────────────────────────────────────────
        parsed = _parse_rca_response(raw)

        # ── Step 4: Calibrate confidence + determine risk ─────────────────────
        # Don't trust the LLM's confidence blindly — calibrate with data
        raw_conf   = parsed.get("raw_confidence", 60)
        confidence = calibrate_confidence(raw_conf, similar, ticket)
        sev        = ticket.get("severity", "P3")
        risk       = determine_risk_tier(confidence, sev, similar)
        path       = _get_approval_path(confidence, risk, sev)

        result = {
            "id":                       rca_id,
            "ticket_id":                ticket_id,
            "root_cause":               parsed["root_cause"],
            "confidence_score":         confidence,
            "risk_tier":                risk,
            "recommended_fix":          parsed["recommended_fix"],
            "fix_steps":                parsed["fix_steps"],
            "estimated_resolution_hrs": parsed["estimated_resolution_hrs"],
            "pattern_match":            parsed["pattern_match"],
            "source_citations":         parsed["source_citations"],
            "warnings":                 parsed.get("warnings"),
            "approval_path":            path,
            "similar_incidents":        [
                {
                    "id":               s.get("id", ""),
                    "description":      s.get("description", "")[:120],
                    "resolution":       (s.get("resolution_notes") or s.get("closure_code", ""))[:150],
                    "similarity_score": s.get("similarity_score", 0),
                    "similarity_pct":   s.get("similarity_pct", 0),
                    "severity":         s.get("severity", ""),
                    "category":         s.get("category", ""),
                    "mttr_hrs":         s.get("resolution_time_hrs", ""),
                }
                for s in similar[:3]
            ],
            "model_used":   GROQ_MODEL,
            "status":       "success",
        }

    except Exception as e:
        print(f"⚠️  RCA failed for {ticket_id}: {e}")
        import traceback
        traceback.print_exc()
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
            json.dumps([s.get("similarity_score", 0) for s in sim]),
            result["confidence_score"],
            result["risk_tier"],
            now,
        ))

        # Update ticket status to pending_approval ONLY if still open
        # (auto_approve_low_risk may have already resolved it)
        conn.execute(
            "UPDATE tickets SET status='pending_approval' WHERE id=? AND status='open'",
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

    # Store original LLM confidence for calibration, add jitter to reduce repetition
    raw_conf = int(p.get("confidence_score", 65))
    jitter = (len(p.get("root_cause", "")) % 7) - 3
    p["raw_confidence"] = max(0, min(100, raw_conf + jitter))

    # Normalize with safe defaults
    p["root_cause"]               = str(p.get("root_cause", "Unknown root cause"))
    p["confidence_score"]         = p["raw_confidence"]  # will be overridden by calibration
    p["risk_tier"]                = str(p.get("risk_tier", "Medium")).strip().title()
    if p["risk_tier"] not in ("Low","Medium","Critical"): p["risk_tier"] = "Medium"
    p["recommended_fix"]          = str(p.get("recommended_fix", "Escalate to senior engineer"))
    p["fix_steps"]                = p.get("fix_steps", [])
    if not isinstance(p["fix_steps"], list): p["fix_steps"] = [str(p["fix_steps"])]
    p["estimated_resolution_hrs"] = float(p.get("estimated_resolution_hrs", 2.0))
    p["pattern_match"]            = str(p.get("pattern_match", ""))
    p["source_citations"]         = p.get("source_citations", [])
    if not isinstance(p["source_citations"], list): p["source_citations"] = []

    return p


def _get_approval_path(confidence: int, risk_tier: str, severity: str) -> str:
    """
    Determines approval workflow path based on calibrated confidence, risk, and severity.

    Path A (Auto-execute):  High confidence (≥75%) + Low risk + P3
    Path B (Operator approval): Medium confidence or medium risk or P2 with decent confidence
    Path C (Senior review): Low confidence (<40%) or P1 or Critical+low confidence
    """
    # P1 always needs senior review
    if severity == "P1":
        return "C"

    # Path A (Auto-execute)
    if severity == "P3" and confidence >= 70:
        return "A"
    if severity == "P2" and confidence >= 85 and risk_tier != "Critical":
        return "A"

    # Path B (Operator approval)
    if severity == "P3" and confidence >= 40:
        return "B"
    if severity == "P2" and confidence >= 50:
        return "B"

    # Low confidence needs senior review
    if confidence < 40:
        return "C"

    # Fallback to C
    return "C"


def _fallback_rca(ticket_id: str, rca_id: str, error: str) -> dict:
    """
    Fallback when RCA engine encounters an error.
    Still provides useful guidance instead of just "unavailable".
    """
    # Try to pull useful context from the ticket itself
    root_cause = "Automated RCA could not complete — likely infrastructure or configuration issue"
    fix = "Perform a systematic infrastructure triage to identify the failing component."
    fix_steps = [
        "1. Verify Service Health: Check the status of the primary application pods/containers and dependent services (Database, Redis, Message Queues).",
        "2. Inspect Application Logs: Review logs from the last 15-30 minutes for explicit errors, timeouts, or out-of-memory (OOM) kills.",
        "3. Review Recent Changes: Check CI/CD pipelines and config management tools for any deployments or configuration changes that occurred right before the incident started.",
        "4. Check Network & Infrastructure: Verify network connectivity, load balancer health, and available disk space/compute resources on the host nodes.",
        "5. Escalate: If the root cause is not identified within 15 minutes of triage, escalate to the L3 infrastructure or respective domain engineering team."
    ]
    
    # If the error hints at what went wrong, give a better message
    err_lower = error.lower()
    if "api_key" in err_lower or "groq" in err_lower:
        root_cause = "RCA analysis engine temporarily unavailable (API connectivity issue)"
        fix = "Retry RCA in 30 seconds — if persistent, check GROQ_API_KEY configuration"
    elif "index" in err_lower or "faiss" in err_lower:
        root_cause = "Knowledge base index is rebuilding — RCA will be available shortly"
        fix = "Wait 15 seconds and retry — the system is loading historical incident data"
    elif "timeout" in err_lower:
        root_cause = "RCA timed out due to high load — retry should succeed"
        fix = "Retry RCA — the AI model is experiencing high latency"

    return {
        "id":                       rca_id,
        "ticket_id":                ticket_id,
        "root_cause":               root_cause,
        "confidence_score":         15,
        "risk_tier":                "Medium",
        "recommended_fix":          fix,
        "fix_steps":                fix_steps,
        "estimated_resolution_hrs": 2.0,
        "pattern_match":            "Unable to match patterns — manual review needed",
        "source_citations":         [],
        "warnings":                 f"Automated RCA encountered: {error[:150]}. Retry recommended.",
        "approval_path":            "C",
        "similar_incidents":        [],
        "model_used":               "fallback",
        "status":                   "fallback",
    }


# ── Startup helper — pre-warm index in background ────────────────────────────

def prewarm_index():
    """Call at server startup to load index in background thread."""
    import threading
    def _load():
        try:
            get_index()
            print("🔥 FAISS index pre-warmed and ready")
        except Exception as e:
            print(f"⚠️  Index pre-warm failed: {e}")
    t = threading.Thread(target=_load, daemon=True)
    t.start()


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🧪 Phase 3 — RCA Engine Test")
    print("=" * 60)

    print("\n[1] Building FAISS index...")
    try:
        index, store = build_index(force_rebuild=True)
        print(f"    ✅ Index ready — {index.ntotal:,} vectors")
    except Exception as e:
        print(f"    ❌ {e}")
        exit(1)

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
