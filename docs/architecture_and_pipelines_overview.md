# TamePND: End-to-End Architecture & Pipelines Overview

TamePND is a Human-Governed Autonomous AI Support System that flips reactive IT support into an AI-led, proactive model. It intelligently predicts incidents, automatically identifies root causes using historical precedents, and conditionally executes fixes with strict human-in-the-loop governance.

This document serves as your complete guide to the inner workings of TamePND, explaining its architecture, core pipelines, and user interaction flows up to the latest features.

---

## 🏗️ 1. The 5-Layer System Architecture

TamePND is structurally segregated into five distinct layers to cleanly separate data ingestion, AI-driven analysis, presentation, and persistence.

- **[Layer 1] Data Ingestion:** Driven by FastAPI, this layer exposes REST and WebSocket endpoints for receiving new incidents manually, via the voice-portal, or via bulk CSV feeds (using 46,600+ historical IT tickets).
- **[Layer 2] Prediction Engine:** Powered by Groq LLaMA 3.3-70B. It performs zero-shot text classification, determining incident severity (P1/P2/P3), category, and risk tier without needing pre-trained rigid classification models.
- **[Layer 3] Root Cause Analysis (RCA) Engine:** Uses a localized vector database (FAISS) and Sentence-Transformers (`all-MiniLM-L6-v2`). It semantically searches past tickets to find historical precedents and synthesizes an actionable, 3-5 step fix using Groq's LLM.
- **[Layer 4] Governance Dashboard:** A lightweight, pure CSS-in-JS React 18 + Vite frontend that monitors the live feed, allows operators to review the RCA, and conditionally approve/reject automated actions.
- **[Layer 5] Learning & Audit:** Uses an SQLite relational database mapping (`opsai.db`) to record infinite audit trails and tune "Trust Calibration." Additionally, every resolved ticket is updated in the FAISS memory vector index to iteratively improve the AI.

---

## ⚙️ 2. Core Operational Pipelines

Here is the exact step-by-step breakdown of how data travels through the system.

### A. The Ingestion & Zero-Shot Prediction Pipeline
**When does it happen?** The moment a support ticket is submitted via `/tickets/ingest` or the Voice Client Portal.

1. **Intake:** The FastAPI server receives the unstructured textual description along with metadata.
2. **Keyword Analysis:** A deterministic function scans the payload for critical markers (e.g., "DB Down", "Outage") instantly.
3. **Prompt Construction:** The server combines `CI_Cat`, `CI_Subcat`, urgency, and impact context to build a strict JSON-only system prompt.
4. **Groq LLaMA Inference:** The system makes a lightning-fast API call to LLaMA 3.3-70B (`temperature=0.1`) extracting Predicted Severity, Category, and incident type.
5. **Confidence Calibration:** The raw confidence score from the LLM is blended with the historical `fix_outcomes` records stored in SQLite and deterministic jitter (-3 to +3) is applied to ensure scores aren't repetitively "robotic".
6. **Path Routing:** The ticket is stored in the DB as `pending_approval` and routed out to the UI via Live Feed polling / WebSockets.

### B. The Semantic RCA Pipeline (Root Cause Analysis)
**When does it happen?** This triggers asynchronously alongside prediction or can be manually triggered by an operator reviewing an incident.

1. **Embedding generation:** The unstructured ticket description is converted into a vector format using `all-MiniLM-L6-v2`.
2. **Vector Search:** The FAISS index (`db/faiss.index`) finds the **Top-3 most similar past incidents** that were successfully resolved.
3. **AI Synthesis:** A specialized prompt passes the current incident and the top 3 historical precedents to Groq LLaMA.
4. **Output Enforcement:** The LLM is strictly constrained to output robust JSON including a 3-5 step `fix_steps` troubleshooting sequence and estimated resolution time based *only* on the retrieved historical resolutions.
5. **Fallback Safety:** If the API fails or times out, the backend gracefully falls back to a deterministic 5-step triage guide instead of failing blindly.

### C. Continuous Learning & Loop-Closure Pipeline
**When does it happen?** When an action is finalized, rolled back, or a ticket is successfully marked 'Resolved'.

1. **Trust Adjustment:** If a human operator clicks `Approve` for an AI-suggested fix, the AI's confidence score for that specific incident category mathematically raises in the `fix_outcomes` table. If the execution is rolled back, the score is penalized.
2. **Memory Updation:** The `add_to_index()` hook grabs the resolved text and resolution context, vectorizes it, and inserts it live into the FAISS memory index and disk store (`memory_store.pkl`). The AI now instantly "knows" this precedent for the next query.

---

## 🚦 3. Adaptive Human-in-the-Loop Governance Flows

When the AI presents a fix to the frontend, it intelligently limits its autonomy utilizing three dynamic workflow paths.

- **Path A (Auto-Execute):** Triggered when the AI is highly confident and the risk tier is Low (e.g., P3 + >70% confidence, or P2 + >85% confidence). The UI renders a **10-second countdown cancel window**. If the human operator does nothing, the script executes autonomously.
- **Path B (Operator Approval):** Triggered for mid-confidence actions (e.g., 40% for P3, 50% for P2). The system pauses completely. The operator must review the RCA panel and explicitly click **Approve** or **Reject**.
- **Path C (Mandatory Senior Review):** Hard-gated for critical incidents like P1 Database Outages or anomalous predictions (<40% confidence). The AI refuses to auto-execute. The UI requires a senior engineer to manually type a textural justification before clicking **Execute with Full Audit Trail**.

---

## 🖱️ 4. Functional UI/UX Interactions

What actually happens when you interact with the Dashboard?

- **Creating an Incident (+ Button):** Floating Ingest Form allows quick testing via 5 presets or manual input. Doing so fires `POST /tickets/ingest`.
- **My Tickets View:** The system leverages advanced Backend Routing to prioritize high-severity incidents. The UI animates confidence bars and conditionally renders a panel for AI-generated incident summaries (LLM generated context over ticket metadata).
- **Clicking on a Ticket in Live Feed:** The Dashboard queries `/tickets/{id}/rca/result` and populates the **RCA Detail Panel**. You will see: The predicted root cause, Top 3 similar incident citations with similarity percentages, and the exact step-by-step resolution roadmap.
- **Approving/Rejecting (`executeAction()`):** Triggers an execution cycle that captures the JSON state before and after the fix, updates the `audit_log`, adjusting the `fix_outcomes` counters, and updates the main SQLite state.
- **Audit Logging:** Every single approval, override, fallback, or LLM inference generates a cryptographically hashed row in the `audit_log` with the operator's session metadata, making all operations fully auditable.

---

## ✨ 5. Latest Cutting-Edge Features

Recent integrations from the April 2026 build branch:

1. **Intelligent Incident Summarization:** The UI now fetches pre-generated structural "Operations Summaries" to bypass log fatigue. They immediately highlight SLA status, key actions required, and predicted root causes at a glance.
2. **Predictive Incident Clustering:** A standalone background scanner (`cluster_detector.py`) watches the ingest queue for groups of similar semantic incidents arriving simultaneously. This actively alerts operators to "bursts" or cascade failures before they become unified P1 incidents.
3. **Voice-Enabled Portal (`/portal`):** Integrated via the Web Speech API. Clients can narrate their issue, which streams transcription, bundles it into the ingestion pipeline, and seamlessly syncs to the Live Governance Feed.
4. **Deterministic Jitter:** We solved predictive plateauing by injecting small cryptographic math jitters to confidence scores, allowing tickets with dense histories to dynamically fan out across Paths A, B, and C optimally.
