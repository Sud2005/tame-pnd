<h1 align="center">
  TamePND — Human-Governed Autonomous AI Support System
</h1>

<p align="center">
  <strong>Built for the Atos AI Hackathon
  COULDNT UPLOAD ENTIRE CODEBASE DUE TO FILE LIMIT SET AT 50 mb PLEASE CHECK THE GITHUB REPO FOR THE FULL CODEBASE</strong><br>
  <em>By Team TamePND (Sudhanshu Narvekar, Swayam Samel)</em>
</p>

---

## 🚀 The Vision

Application support today is reactive, ticket-driven, and highly dependent on expert intervention, resulting in high mean time to resolution and severe knowledge attrition risks. 

**TamePND** completely flips this paradigm. We have built an AI-led support model that instantly predicts incidents, automatically identifies root causes through semantic analysis of 46,000+ historical IT tickets, recommends fixes, and executes them—while maintaining strict human-in-the-loop governance, 10-second rollback windows, and immutable audit trails for enterprise safety.

---

## 🌟 The "Wow" Factor: Key Features

### 1. Zero-Shot Incident Prediction
The moment a support ticket is ingested (or spoken via our client portal), our prediction engine—powered by **Groq LLaMA 3.3-70B**—instantly analyzes the unstructured text to determine the exact severity (P1/P2/P3), category, and risk tier **without** requiring pre-trained, rigid classification models. It is fully dynamic and blazing fast.

### 2. Semantic Root Cause Analysis (RCA) Engine
Instead of relying on AI hallucinations, TamePND uses an **Offline-First Vector Search**. We embedded a real-world dataset of 46,606 Kaggle ITSM tickets using localized `all-MiniLM-L6-v2` embeddings into a **FAISS** index. When a new issue arises, the system retrieves the Top-3 most similar historical incidents in milliseconds, feeds them to the LLM, and synthesizes a strict 3-5 step actionable fix based *only* on proven historical precedents.

### 3. Adaptive Human-in-the-Loop Governance
The system does not execute blindly; it intelligently limits its autonomy based on calculated risk:
- **Path A (Auto-Execute):** High-confidence, low-risk fixes trigger a safe 10-second countdown (cancel window) before executing autonomously.
- **Path B (Operator Approval):** Mid-confidence fixes pause, requiring a human operator to click "Approve" or "Reject".
- **Path C (Mandatory Senior Review):** Critical issues (like P1 DB Outages) are hard-gated, requiring explicit textual justification from a senior engineer before proceeding.

### 4. Continuous Learning & Trust Calibration
TamePND gets smarter every time it's used. It organically tracks `fix_outcomes`. When humans approve an AI-recommended fix, the AI's confidence score for that category mathematically increases. If a fix is rolled back, the system penalizes it. Every newly resolved ticket is immediately vectorized and appended to the AI's memory.

### 5. Infinite Traceability & The Audit Trail
100% of AI decisions are traceable, auditable, and reversible. We provide a real-time Audit Trail screen that logs all actions: predictions, overrides, approvals, and autonomous executions. We capture pre-state/post-state snapshots, operator IDs, and confidence scores, proving true enterprise readiness.

### 6. Voice-Enabled Client Portal
A sleek, standalone client-facing portal (`/portal`) equipped with the **Web Speech API**. End users can simply describe their issue out loud, and the AI streams the conversation, generates the ticket, and pushes it directly into the Governance Live Feed via WebSockets.

### 7. NEW: Intelligent Incident Summarization
The system now automatically generates structured "Operations Summaries" for every incident. These summaries distill complex technical logs into concise, actionable handoff reports, including SLA status, key actions taken, and AI-predicted root causes.

### 8. NEW: Predictive Incident Clustering
Integrates a standalone scanner that identifies bursts of similar incidents in real-time. This allows operators to detect larger outages before they escalate into high-severity P1 crises.

---

## 🏗 System Architecture (5 Layers)

```text
[Layer 1] Data Ingestion        → FastAPI + Internal Ticket Dataset (46,606 rows)
[Layer 2] Prediction Engine     → Groq LLaMA 3.3-70B zero-shot classification
[Layer 3] RCA Engine            → FAISS + all-MiniLM-L6-v2 + Groq Synthesis
[Layer 4] Governance Dashboard  → React 18 + Vite (Dependency-free architecture)
[Layer 5] Learning & Audit      → SQLite + fix_outcomes mapping + FAISS memory update
```

---

## 🛠 Tech Stack

- **Backend:** Python 3.11, FastAPI, WebSockets
- **Database:** SQLite (Relational), FAISS (Vector)
- **AI & NLP:** Groq SDK, Sentence-Transformers (`all-MiniLM-L6-v2`)
- **Frontend:** React 18, Vite.js (Custom CSS-in-JS, strictly avoiding bulky UI libraries for a highly-performant, 1000-line modular design).

---

## 🏁 How to Run Locally

### 1. Prerequisites & Installation
Ensure you have Python 3.11+ and Node.js installed.
```bash
# Clone the repository
git clone https://github.com/Sud2005/tame-pnd.git
cd tame-pnd

# Setup Python Backend
python -m venv venv
.\venv\Scripts\activate  # Windows
# source venv/bin/activate # Mac/Linux
pip install -r requirements.txt

# Setup Frontend
cd dashboard
npm install
cd ..
```

### 2. Environment Setup
Create a `.env` file in the root directory and add your Groq API key:
```env
GROQ_API_KEY="gsk_your_api_key_here..."
```

*(Note: The `db/` folder already contains a pre-compiled FAISS index and the 46k seeded SQLite database perfectly calibrated for the hackathon demo).*

### 3. Spin Up the Servers
Open two terminals to run the system:

**Terminal 1 — Backend API**
```bash
.\venv\Scripts\activate
uvicorn ingestion:app --reload --port 8000
```

**Terminal 2 — Frontend Governance Dashboard**
```bash
cd dashboard
npm run dev
```

### 4. Application URLs
- 🎛 **Admin Dashboard (React):** [http://localhost:3000](http://localhost:3000)
- 🎙 **Voice Client Portal:** [http://localhost:8000/portal](http://localhost:8000/portal)
- ⚙️ **API Docs (Swagger UI):** [http://localhost:8000/docs](http://localhost:8000/docs)

---

<p align="center">
  <i>"Every AI decision is traceable, auditable, and reversible."</i>
</p>
