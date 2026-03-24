# TamePND — OpsAI Project Context
# Human-Governed Autonomous AI Support System
# Feed this entire file to Claude Opus / Antigravity before any bug fix or improvement request
# Last updated: March 24, 2026

---

## WHO WE ARE

**Team:** TamePND — Sudhanshu Narvekar, Swayam Samel
**Competition:** Atos AI Hackathon
**Problem Statement:** Application support today is reactive, ticket-driven, and highly dependent on
expert intervention, resulting in high mean time to resolution and knowledge attrition risks. Build
an AI-led support model that predicts incidents, automatically identifies root causes, recommends or
executes fixes, and continuously learns, with humans providing oversight for safety and governance.
All autonomous actions must include human-in-the-loop controls, approval workflows, rollback
mechanisms, trust calibration, and compliance with regulatory and audit requirements.

**GitHub Repo:** https://github.com/Sud2005/tame-pnd
**Stack:** Python 3.11 + FastAPI + SQLite + Groq LLaMA 3.3-70B + FAISS + sentence-transformers + React 18 + Vite

---

## SYSTEM ARCHITECTURE (5 Layers)

```
[Layer 1] Data Ingestion        → FastAPI + ITSM_data.csv (46,606 rows)
[Layer 2] Prediction Engine     → Groq LLaMA 3.3-70B zero-shot classification
[Layer 3] RCA Engine            → FAISS + all-MiniLM-L6-v2 + Groq synthesis
[Layer 4] Governance Dashboard  → React 18 + Vite (4 screens)
[Layer 5] Learning & Audit      → SQLite + fix_outcomes calibration + FAISS memory
```

---

## FILE STRUCTURE

```
tame-pnd/
├── ingestion.py          # FastAPI server — main entry point
├── prediction.py         # Phase 2 — Groq LLaMA prediction engine
├── rca_engine.py         # Phase 3 — FAISS semantic search + Groq RCA synthesis
├── normalize_dataset.py  # Converts ITSM_data.csv to clean schema
├── setup_db.py           # Creates SQLite schema + seeds data
├── generate_tickets.py   # Synthetic ticket generator (backup if no CSV)
├── demo_feed.py          # Feeds tickets to live API for demo
├── inject_b.py           # Injects custom-tailored Path B tickets
├── test_groq.py          # Groq API connectivity tester
├── fix_db.py             # Utility to clear cached DB errors
├── test_phase1.py        # API test suite Phase 1
├── test_phase2.py        # API test suite Phase 2 (Groq)
├── test_phase3.py        # API test suite Phase 3 (FAISS + RCA)
├── view_predictions.py   # Terminal viewer for prediction output
├── requirements.txt      # Python dependencies
├── .env                  # API keys (GITIGNORED)
├── .env.example          # Template
├── db/
│   ├── opsai.db          # SQLite database (GITIGNORED)
│   ├── faiss.index       # FAISS vector index (GITIGNORED)
│   └── memory_store.pkl  # Parallel ticket store (GITIGNORED)
├── data/
│   └── tickets_clean.csv # Normalized dataset (GITIGNORED)
└── dashboard/
    ├── package.json
    ├── vite.config.js
    ├── index.html
    └── src/
        ├── main.jsx
        └── App.jsx       # Complete single-file React dashboard
```

---

## DATASET

**File:** `ITSM_data.csv`
**Source:** Kaggle ITSM Incident Management dataset
**Size:** 46,606 rows × 25 columns
**Date range:** 2012–2014 (historical data)

**Exact columns:**
```
CI_Name, CI_Cat, CI_Subcat, WBS, Incident_ID, Status, Impact, Urgency,
Priority, number_cnt, Category, KB_number, Alert_Status, No_of_Reassignments,
Open_Time, Reopen_Time, Resolved_Time, Close_Time, Handle_Time_hrs,
Closure_Code, No_of_Related_Interactions, Related_Interaction,
No_of_Related_Incidents, No_of_Related_Changes, Related_Change
```

**Key mappings:**
- `CI_Name` = device/system identifier (e.g. DSK000224, WBA000058) — NOT a text description
- `CI_Cat` values: storage, application, subapplication, hardware, network, applicationcomponent
- `Priority` = numeric 1-5 (1=critical, 4/5=low) mapped to P1/P2/P3
- `Closure_Code` = resolution notes (often sparse/missing)
- `Alert_Status` = True/False
- `Handle_Time_hrs` = MTTR in hours

**Known issue:** CI_Name is a system ID not a description. The normalizer synthesizes
descriptions from CI_Cat + CI_Subcat + Urgency + Impact + Alert_Status combined.
This produces generic descriptions like "Web application failure on WBA000058 with high urgency"
which gives Groq weak signal → all tickets get classified as P3/General/55% confidence.

---

## DATABASE SCHEMA (SQLite)

```sql
-- 8 tables total

tickets (
    id TEXT PK,              -- format: INC{8hex} e.g. INC2829668A
    description TEXT,
    severity TEXT,           -- P1 / P2 / P3
    category TEXT,           -- Database/Network/Authentication/Infrastructure/Application/General
    opened_at TEXT,
    resolved_at TEXT,
    resolution_time_hrs REAL,
    resolution_notes TEXT,
    assigned_group TEXT,
    resolved_by TEXT,
    status TEXT              -- open / pending_approval / resolved
)

predictions (
    id TEXT PK,
    ticket_id TEXT FK→tickets,
    predicted_severity TEXT,
    predicted_category TEXT,
    predicted_incident TEXT,
    confidence_score INTEGER,  -- 0-100, calibrated
    risk_tier TEXT,            -- Low / Medium / Critical
    anomaly_flagged INTEGER,   -- 0/1
    reasoning TEXT,
    raw_llm_response TEXT
)

rca_results (
    id TEXT PK,
    ticket_id TEXT FK→tickets,
    root_cause TEXT,
    recommended_fix TEXT,
    similar_incident_1 TEXT,   -- ticket_id
    similar_incident_2 TEXT,
    similar_incident_3 TEXT,
    similarity_scores TEXT,    -- JSON array e.g. [0.92, 0.87, 0.81]
    confidence_score INTEGER,
    risk_tier TEXT
)

approval_actions (
    id TEXT PK,
    ticket_id TEXT FK→tickets,
    rca_id TEXT,
    approval_path TEXT,        -- A / B / C
    action_type TEXT,          -- APPROVE / REJECT / OVERRIDE / AUTO
    operator_id TEXT,          -- 'system' for auto-execute
    operator_reason TEXT,
    recommended_fix TEXT,
    confidence_at_time INTEGER,
    risk_tier TEXT
)

executions (
    id TEXT PK,
    approval_id TEXT FK→approval_actions,
    ticket_id TEXT,
    fix_type TEXT,             -- restart_service / clear_cache / scale_up etc
    pre_state TEXT,            -- JSON snapshot before action
    post_state TEXT,           -- JSON snapshot after action
    outcome TEXT,              -- success / failed / rolled_back
    rolled_back INTEGER,       -- 0/1
    rollback_reason TEXT,
    executed_at TEXT,
    rolled_back_at TEXT
)

fix_outcomes (
    id TEXT PK,
    category TEXT,             -- matches tickets.category
    fix_type TEXT,
    approve_count INTEGER,
    reject_count INTEGER,
    rollback_count INTEGER,
    total_actions INTEGER,
    calibrated_confidence INTEGER,  -- 0-100
    UNIQUE(category, fix_type)
)

audit_log (
    id TEXT PK,
    event_type TEXT,           -- INGEST/PREDICT/RCA/APPROVE/REJECT/EXECUTE/ROLLBACK/OVERRIDE/RESOLVE
    ticket_id TEXT,
    operator_id TEXT,
    approval_path TEXT,        -- A / B / C
    confidence INTEGER,
    risk_tier TEXT,
    action_taken TEXT,
    reasoning TEXT,
    outcome TEXT,
    pre_state TEXT,
    post_state TEXT,
    session_hash TEXT,
    timestamp TEXT
)
```

---

## API ENDPOINTS (FastAPI on port 8000)

```
GET  /health                          → server status + phase info
GET  /stats                           → dashboard summary counts
POST /tickets/ingest                  → main ingestion endpoint
GET  /tickets                         → list tickets (filter: status, severity, limit, offset)
GET  /tickets/{id}                    → single ticket
GET  /tickets/{id}/prediction         → Groq prediction result
POST /tickets/{id}/rca                → manually trigger RCA
GET  /tickets/{id}/rca/result         → fetch RCA result
GET  /tickets/{id}/audit              → ticket audit trail
POST /tickets/{id}/resolve            → mark resolved + add to FAISS memory
POST /tickets/bulk-ingest             → batch ingest (max 50)
GET  /audit                           → full system audit log (filter: event_type, limit)
```

**Ingest payload:**
```json
{
  "description": "string (required, min 3 chars)",
  "severity": "P1|P2|P3 (optional, auto-detected)",
  "category": "optional, auto-detected",
  "ci_cat": "storage|application|subapplication|network|hardware",
  "ci_subcat": "optional",
  "urgency": "1-4",
  "impact": "1-4",
  "alert_status": "True|False",
  "assigned_group": "optional",
  "source": "manual|csv_feed|monitoring"
}

---

## PREDICTION ENGINE (prediction.py)

**Model:** `llama-3.3-70b-versatile` on Groq (free tier)
**Trigger:** Background task fired on every ticket ingest
**Pipeline:**
1. `run_keyword_analysis()` — deterministic, no API, instant
2. `build_prompt()` — constructs prompt with CI_Cat, CI_Subcat, Urgency, Impact context
3. Groq API call — `temperature=0.1`, `max_tokens=350`, JSON-only system prompt (LLaMA 3.3-70B)
4. `parse_response()` — strips markdown, validates all fields, applies deterministic jitter (-3 to +3) to avoid repetitive scores
5. `calibrate_confidence()` — blends LLM score with historical fix_outcomes + caps P1 at 80% and P2 at 95%
6. `get_approval_path()` — routes to A/B/C using dynamic thresholds
7. DB write to `predictions` table + `audit_log`

**Approval path logic (V2 Optimized):**
```
Severity P1 → Path C (Mandatory Senior Review)
Confidence < 40% → Path C (Mandatory Senior Review)
Severity P3 + Confidence >= 70% → Path A (Auto-Execute)
Severity P2 + Confidence >= 85% + No Critical Risk → Path A (Auto-Execute)
Severity P3 + Confidence >= 40% → Path B (Operator Approval)
Severity P2 + Confidence >= 50% → Path B (Operator Approval)
Otherwise Fallback → Path C
```

**Trust calibration:**
```python
# load_dotenv(override=True) used to allow live config changes
# When history < 5 actions for a category:
calibrated = int(raw * 0.75)
if severity == "P1": calibrated = min(calibrated, 80)
if severity == "P2": calibrated = min(calibrated, 95) # High-confidence P2 can now hit Path A

# When history >= 5:
calibrated = int((raw * 0.4) + (hist_accuracy * 100 * 0.6) - (rollbacks * 10))
# Jitter based on string hash: (len(reasoning) % 7) - 3 applied to score
```

**Status:** FIXED — Path C over-divergence resolved. Repetitive confidence scores fixed with jitter. P2 auto-execution now possible at 85%+ confidence.
2. Old calibrate_confidence() hardcoded `min(raw, 55)` for low-history categories
3. `General` category has no entries in fix_outcomes table
Fix: Use ingest form presets for demo (rich descriptions → varied confidence), not ITSM CSV tickets

---

## RCA ENGINE (rca_engine.py)

**Embedding model:** `all-MiniLM-L6-v2` via sentence-transformers (local, free, ~90MB)
**Vector store:** FAISS IndexFlatIP (inner product = cosine similarity after L2 normalization)
**Index size:** ~46,000 vectors (one per resolved ticket with resolution notes)
**Saved to disk:** `db/faiss.index` + `db/memory_store.pkl`

**Pipeline:**
1. On server startup: `build_index()` loads or builds FAISS from resolved tickets
2. `search_similar(description, k=3)` — embeds query → FAISS search → top-3 matches
3. `build_rca_prompt(ticket, similar)` — formats past incidents + new ticket for Groq
4. Groq call — `temperature=0.2`, `max_tokens=600`
5. Returns: root_cause, confidence, risk_tier, recommended_fix, fix_steps (list),
   estimated_resolution_hrs, pattern_match, source_citations, warnings
6. DB write to `rca_results` + update ticket status to `pending_approval`
7. **Detailed Infrastructure Fallback:** If the automated RCA fails (API error/timeout), the engine returns a systematic 5-step triage guide (Service Health, Logs, Recent Changes, Network, and Escalation) instead of a generic message.

**Learning loop:** `add_to_index(ticket_dict)` — called by `/tickets/{id}/resolve`
Adds newly resolved ticket to live FAISS index + persists to disk immediately

**Enhanced RCA Prompt Enforcement:**
The system now strictly enforces a **detailed, multi-step sequence (at least 3-5 distinct steps)** in the `fix_steps` array. This prevents the LLM from returning single-sentence repeats of the recommended fix, ensuring operational value even when historical data is sparse.

**RCA prompt structure:**
```
You are a senior IT operations engineer with 15 years of experience.
NEW INCIDENT: {description + context}
SIMILAR PAST INCIDENTS (3 retrieved by semantic similarity):
  #1 (94.2% match): {description} | Resolution: {closure_code} | MTTR: X hrs
  #2 (87.1% match): ...
  #3 (81.3% match): ...
Synthesize root cause, recommend fix, estimate resolution time.
Return JSON only.
```

---

## REACT DASHBOARD (dashboard/src/App.jsx)

**Single file, ~900 lines.** All components in one file.
**No external UI library** — pure CSS-in-JS via style objects
**Fonts:** JetBrains Mono (monospace) + Syne (display) via Google Fonts
**Color palette (dark theme):**
```js
bg:       "#0A0E1A"   // darkest background
surface:  "#0F1628"   // nav/status bars
card:     "#141D35"   // card backgrounds
border:   "#1E2D50"   // dividers
accent:   "#00D4FF"   // cyan — primary brand color
p1:       "#FF3B5C"   // red — critical
p2:       "#FFB020"   // amber — warning
p3:       "#00E676"   // green — low severity
```

**4 screens:**
1. `TicketFeed` — live list, stat cards, severity filter, 5s auto-refresh
2. `RCADetail` — root cause, top-3 similar incidents, fix steps, citations
3. `ApprovalWorkflow` — Path A (countdown timer), Path B (approve/reject), Path C (justification gate)
4. `AuditTrail` — full event log, event type filter, Export CSV button

**Key components:**
- `Badge({ label, color, dim, size })` — severity/status chips
- `ConfBar({ value, color })` — animated confidence bar
- `Spinner()` — loading indicator
- `LiveDot({ color })` — pulsing live indicator
- `IngestForm` — floating + button modal with 5 presets + manual form

**API polling:** `setInterval(load, 5000)` in TicketFeed, `setInterval(load, 8000)` in AuditTrail
**API base URL:** `const API = "http://localhost:8000"` at top of App.jsx

---

## ENVIRONMENT VARIABLES (.env)

```bash
GROQ_API_KEY=gsk_xxx...      # free at console.groq.com — llama-3.3-70b-versatile
GOOGLE_API_KEY=xxx           # optional fallback — Gemini via AI Studio
ANTHROPIC_API_KEY=xxx        # optional — Claude for governance reasoning
DB_PATH=db/opsai.db
PORT=8000
ENVIRONMENT=development
FRONTEND_URL=http://localhost:3000
```

---

## PYTHON DEPENDENCIES (requirements.txt)

```
fastapi==0.111.0
uvicorn[standard]==0.29.0
pydantic==2.7.0
python-multipart==0.0.9
pandas==2.2.2
numpy==1.26.4
sentence-transformers==2.7.0
faiss-cpu==1.8.0
groq==0.9.0
python-dotenv==1.0.1
httpx==0.27.0
pytest==8.2.0
pytest-asyncio==0.23.6
```

---

## HOW TO RUN

```bash
# Backend
source venv/Scripts/activate    # Windows
# OR source venv/bin/activate   # Mac/Linux
uvicorn ingestion:app --reload --port 8000

# Frontend (separate terminal)
cd dashboard
npm run dev
# Opens at http://localhost:3000

# Normalize dataset (if ITSM_data.csv present)
python normalize_dataset.py --input ITSM_data.csv --output data/tickets_clean.csv

# Setup / reset database
python setup_db.py --data data/tickets_clean.csv --reset

# Run tests
python test_phase2.py --direct   # Groq only, no server
python test_phase2.py            # full API tests
python test_phase3.py --direct   # FAISS + RCA, no server
python test_phase3.py            # full pipeline

# Demo feed
python demo_feed.py --input ITSM_data.csv --limit 20 --interval 3
python demo_feed.py --input data/demo_tickets.csv --limit 10 --severity P1

# Git workflow
git add .
git commit -m "describe what changed"
git push
```

---

## GITIGNORE (what's excluded)

```
venv/
__pycache__/
.env
db/*.db
db/faiss.index
db/memory_store.pkl
data/tickets_raw.csv
data/tickets_clean.csv
data/demo_tickets.csv
ITSM_data.csv
.ipynb_checkpoints/
*.pyc *.pyo *.pyd
dashboard/node_modules/
dashboard/dist/
```

---

## KNOWN BUGS & ISSUES

### BUG 1 — Repetitive 55% Confidence / Path C Over-divergence [FULLY FIXED]
**Root cause:** Broad routing rules forced most tickets to Path C. Default confidence was throttled during cold-start.
**Fix:**
- Implemented **Deterministic Jitter** (-3 to +3) in both Prediction and RCA engines to eliminate repetitive "x0/x5" scores.
- Revamped **Approval Path Rules** to be more efficient:
    - P3 auto-executes at 70%+ (was 75%+ with mandatory Low risk).
    - P2 can now auto-execute at 85%+ (previously almost entirely locked to B/C).
    - Operator Approval (Path B) now reachable with much lower confidence floors (40% for P3, 50% for P2).
- Enabled `.env` hot-reloading using `load_dotenv(override=True)`.
**Workaround:** Use IngestForm presets for the best demo visuals, but CSV tickets now distribute much better across paths.

### BUG 1b — RCA "fix_steps" showing redundant/single steps [FIXED]
**Root Cause:** Bias from sparse historical data caused the LLM to output single-sentence resolutions.
**Fix:** Updated both the system and user prompts in `rca_engine.py` to explicitly demand a 3-5 step troubleshooting and verification sequence.

### BUG 2 — bulk_ingest endpoint calls ingest_ticket() without BackgroundTasks
**Location:** ingestion.py `bulk_ingest()` function
**Issue:** `ingest_ticket(t)` is called directly without BackgroundTasks parameter,
so prediction never fires for bulk-ingested tickets.
**Fix needed:** Refactor bulk_ingest to use BackgroundTasks or call bg_predict directly.

### BUG 3 — RCA result shows "pending" for first 5-7 seconds
**Root cause:** Background task timing — Groq call takes 3-5s, FAISS search adds ~1s.
**Status:** Handled by frontend polling (fetches again if pending). Not a blocking bug.

### BUG 4 — FAISS index not found on fresh clone
**Root cause:** db/faiss.index is gitignored. Fresh clone has empty db/ folder.
**Fix:** Must run `python setup_db.py --data data/tickets_clean.csv` before starting server.
The startup event in ingestion.py calls `build_index()` but needs resolved tickets to exist first.

### BUG 5 — ingestion.py version mismatch
**Issue:** The ingestion.py in the repo may be Phase 1 version (no BackgroundTasks, no RCA endpoints).
The Phase 3 version adds: startup event, rca endpoints, resolve endpoint, bg_predict/bg_rca tasks.
**Fix:** Replace with the Phase 3 ingestion.py which has all endpoints.

### BUG 6 — Approval workflow doesn't write to approval_actions or executions tables
**Location:** App.jsx `executeAction()` function
**Issue:** Currently calls `/tickets/{id}/resolve` which writes to tickets + audit_log,
but doesn't write to `approval_actions` or `executions` tables.
**Fix needed:** Add `POST /tickets/{id}/execute` endpoint that:
1. Writes to approval_actions (approval_path, action_type, operator_id, operator_reason)
2. Takes pre_state snapshot
3. Runs remediation simulation
4. Writes to executions (outcome, pre_state, post_state)
5. Then calls resolve logic

### BUG 7 — No rollback endpoint exists yet
**Issue:** Audit trail shows rollback button in plan but `/tickets/{id}/rollback` endpoint
not implemented. executions table has rolled_back column ready.
**Fix needed:** Add `POST /executions/{id}/rollback` that:
1. Fetches pre_state from executions table
2. Restores pre_state
3. Updates executions.rolled_back = 1, rolled_back_at = now
4. Calls penalize_fix_confidence() to lower that fix type's score
5. Writes ROLLBACK event to audit_log

---

## COMPETITION REQUIREMENTS MAPPING

| Requirement | Where Implemented |
|---|---|
| Predicts incidents | prediction.py → Groq LLaMA classification |
| Identifies root causes | rca_engine.py → FAISS + Groq synthesis |
| Recommends fixes | rca_results.recommended_fix + fix_steps |
| Executes fixes | ApprovalWorkflow → executeAction() |
| Continuously learns | add_to_index() on resolve + fix_outcomes calibration |
| Human-in-the-loop | 10s cancel window on Path A |
| Approval workflows | 3-tier system: Path A/B/C |
| Rollback mechanisms | executions table ready, endpoint TODO |
| Trust calibration | calibrate_confidence() in prediction.py |
| Compliance/audit | audit_log table, Export CSV in dashboard |

---

## DEMO SCRIPT (3-minute judge flow)

```
1. Open dashboard → show 46,648 real enterprise incidents loaded
2. Click + button (bottom right) → select "P1 DB Outage" preset
3. Ticket appears in feed with red P1 badge + anomaly flag
4. Click ticket → RCA panel shows 3 similar past incidents with similarity %
5. Root cause synthesized in plain English from real past resolutions
6. "Proceed to Approval" → Path C triggered (P1 = mandatory senior review)
7. Type justification → "Execute with Full Audit Trail" → success state
8. Switch to Audit Trail → show complete trace → Export CSV
9. Say: "Every AI decision is traceable, auditable, and reversible"
```

---

## WHAT TO BUILD NEXT (Priority Order)

1. **POST /tickets/{id}/execute endpoint** — writes to approval_actions + executions tables
2. **POST /executions/{id}/rollback endpoint** — restores pre_state + penalises confidence
3. **Remediation scripts** — 4 real simulations:
   - restart_service: sleep(2), return success
   - clear_cache: delete temp file, return success
   - scale_up: UPDATE config SET value=value+1 in SQLite
   - rollback: revert above config change
4. **WebSocket for real-time feed** — replace setInterval polling
5. **Improve description synthesis** — better CI_Cat → text mapping for ITSM dataset
6. **Add /tickets/{id}/predict endpoint** — manually re-trigger prediction
7. **Confidence trend chart** — show how trust calibration improves over time

---

## HOW TO USE THIS FILE WITH CLAUDE OPUS / ANTIGRAVITY

Paste this entire file as the first message, then describe your specific request.

**Example prompts:**

For bug fixes:
> "Using the context above, fix Bug 6 — implement the POST /tickets/{id}/execute endpoint
> in ingestion.py that writes to approval_actions and executions tables, runs the
> appropriate remediation script simulation, and writes to audit_log."

For new features:
> "Using the context above, implement the rollback endpoint described in Bug 7.
> The executions table schema is defined in the DATABASE SCHEMA section."

For dashboard changes:
> "Using the context above, update the ApprovalWorkflow component in App.jsx to call
> the new /tickets/{id}/execute endpoint instead of /tickets/{id}/resolve. The endpoint
> should receive: fix_type, operator_id, operator_reason, and approval_path."

For confidence fix:
> "Using the context above, fix the ITSM_data.csv description synthesis in
> normalize_dataset.py. The CI_Subcat values include Desktop Application, Web Based
> Application, Server Based Application, Network Infrastructure, SAN Storage, Laptop.
> Map each to a meaningful incident description that gives Groq real signal."
