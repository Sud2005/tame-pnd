# TamePND — Claude Opus Context Prompt (Token-Optimized)
# Paste this as your FIRST message to Claude Opus / Antigravity
# Then immediately follow with your specific request on the next line
# Designed to stay under 2,000 tokens while preserving maximum context

---

## PROJECT: TamePND — Human-Governed AIOps (Hackathon MVP)

**Stack:** FastAPI + SQLite + Groq LLaMA-3.3-70B + FAISS + sentence-transformers + React 18 + Vite  
**Repo:** https://github.com/Sud2005/tame-pnd  
**API:** http://localhost:8000  
**Frontend:** http://localhost:3000 (dashboard) | client_portal.html (user portal)  
**Run:** `uvicorn ingestion:app --reload --port 8000` + `cd dashboard && npm run dev`

---

## FILE MAP
```
ingestion.py       — FastAPI server, all endpoints
prediction.py      — Groq LLaMA classification + trust calibration
rca_engine.py      — FAISS semantic search + Groq RCA synthesis
dashboard/src/App.jsx — React dashboard (6 screens, ~1980 lines)
client_portal.html — Standalone user portal (no build step)
setup_db.py        — DB schema + seeding
normalize_dataset.py — ITSM CSV normalizer
```

---

## DB SCHEMA (SQLite)
```
tickets: id, description, severity(P1/P2/P3), category, opened_at, resolved_at,
         resolution_notes, status(open/pending_approval/escalated/resolved), created_at
predictions: ticket_id, predicted_severity, predicted_category, confidence_score,
             risk_tier, anomaly_flagged, reasoning, approval_path
rca_results: ticket_id, root_cause, recommended_fix, fix_steps(JSON), similar_incident_1/2/3,
             similarity_scores(JSON), confidence_score, risk_tier, approval_path
fix_outcomes: category, fix_type, approve_count, reject_count, rollback_count, calibrated_confidence
audit_log: event_type(INGEST/PREDICT/RCA/APPROVE/REJECT/EXECUTE/ROLLBACK/ESCALATE/RESOLVE),
           ticket_id, operator_id, approval_path, confidence, risk_tier, action_taken, outcome
```

---

## ALL API ENDPOINTS
```
GET  /health                      GET  /stats
POST /tickets/ingest              GET  /tickets  ?exclude_resolved&severity&status&limit&offset
GET  /tickets/search  ?q&severity&category&status&limit&offset
GET  /tickets/overview            GET  /tickets/escalated
GET  /tickets/{id}                GET  /tickets/{id}/prediction
POST /tickets/{id}/rca            GET  /tickets/{id}/rca/result
POST /tickets/{id}/resolve        POST /tickets/{id}/reject
POST /tickets/{id}/rollback       POST /tickets/{id}/escalate
POST /tickets/{id}/chat           GET  /tickets/{id}/audit
GET  /audit  ?event_type&limit
```

---

## KEY LOGIC

**Severity routing (ingestion.py):**
- Explicit severity sent → ALWAYS respected, keyword engine skipped
- No severity → exact-phrase keyword scan → default P3
- Keyword phrases: P1=["production down","all users locked out","complete outage",...] P2=["partial outage","subset of users affected",...]

**Approval path (prediction.py get_approval_path):**
- P1 → always Path C
- Critical risk + confidence <70 → C; Critical + conf≥70 → B  
- P3 + Low risk + conf≥82 → Path A (auto-execute)
- P2 or Medium → Path B
- Default → B

**Trust calibration (prediction.py calibrate_confidence):**
- <5 history: `int(raw * 0.80)` (no hardcoded floor)
- ≥5 history: `int((raw*0.4) + (hist_accuracy*100*0.6) - (rollbacks*10))`
- P1 cap: 80, P2 cap: 90, P3: uncapped

**FAISS index (rca_engine.py):**
- Embeds: description + CI_Cat + CI_Subcat + resolution_notes
- 3-tier fallback: resolved+notes → all resolved → all tickets
- add_to_index() called on /resolve → live learning

**Chat endpoint (/tickets/{id}/chat):**
- Groq LLaMA-3.3-70B, max_tokens=180, temp=0.4
- History capped at last 5 turns (token saving)
- Returns escalate_requested flag on human-intent keywords
- System prompt: ~120 tokens with ticket context

**Voice (client_portal.html):**
- TTS: Web Speech API SpeechSynthesisUtterance (free, browser-native)
- STT: Web Speech API SpeechRecognition (free, Chrome/Edge)
- No external API needed for voice

---

## DASHBOARD SCREENS (App.jsx)
```
01 LIVE FEED      — TicketFeed + RCADetail split, exclude_resolved, 30-day filter
02 RCA DETAIL     — root cause, similar incidents, fix steps, proceed to approval
03 APPROVAL       — Path A (10s countdown+cancel), Path B (approve/reject+reason), Path C (justification gate)
04 AUDIT TRAIL    — full event log, CSV export, event type filter
05 MEMORY 46K     — searchable/filterable view of all 46,000 ITSM tickets
06 HOW IT WORKS   — live animated pipeline walkthrough (AIExplainer)
```

**Color palette:** bg=#0A0E1A surface=#0F1628 card=#141D35 border=#1E2D50  
accent=#00D4FF p1=#FF3B5C p2=#FFB020 p3=#00E676 text=#E8EDF8 dim=#6B7A9E  
**Fonts:** JetBrains Mono + Syne (Google Fonts)

---

## CLIENT PORTAL (client_portal.html)
Standalone HTML, no build step. Open directly in browser.
- Tab 1: Raise Ticket (form + 5 presets, saves to localStorage)
- Tab 2: My Tickets (live status, polls every 10s, ticket detail with solution/timeline)
- Tab 3: Escalated (shows all escalated tickets with engineer assignment)
- Floating chat bubble → voice assistant modal (Groq chat + Web Speech TTS/STT)
- Auto-offers escalation when user says "human/engineer/not working/escalate"

---

## KNOWN ACTIVE BUGS
1. `bulk_ingest` calls `ingest_ticket()` without BackgroundTasks → predictions don't fire
2. `approval_actions` + `executions` tables never written to (only audit_log + tickets)
3. No WebSocket → feed uses setInterval(5000ms) polling
4. `fix_steps` from Groq sometimes returns single string not array → parse_rca_response normalizes it

---

## HOW TO USE THIS PROMPT

After pasting, write your request directly. Examples:

**Bug fix:**
> Fix bulk_ingest to use BackgroundTasks so predictions fire for each ticket.

**New feature:**
> Add a WebSocket endpoint at /ws that broadcasts new ticket IDs when ingested.
> Update TicketFeed in App.jsx to use WebSocket instead of setInterval.

**RCA improvement:**
> The fix_steps in rca_engine.py are too short. Update build_rca_prompt to demand
> 4-6 detailed steps with verification commands, not just action labels.

**Dashboard fix:**
> The client_portal.html My Tickets tab doesn't show tickets submitted more than
> 30 days ago because list_tickets has a date filter. Add an optional param
> ?all=true to bypass the 30-day filter for the client portal fetch.

**Approval workflow:**
> After Path A auto-executes successfully, add a 30-second window showing a
> ROLLBACK countdown in the result screen. If operator clicks rollback within 30s,
> call POST /tickets/{id}/rollback automatically.
