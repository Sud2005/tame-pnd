# TamePND: Core Features for Hackathon Demo Video

This document outlines the standout capabilities of the TamePND platform. Focus your hackathon demo video on these primary and secondary features to highlight the blend of **autonomous AI** and **strict human governance**.

---

## 🌟 Top Core Features (The "Wow" Factor)

### 1. Zero-Shot Incident Prediction
- **What it does:** The moment a new ticket is ingested, the prediction engine (powered by Groq LLaMA 3.3-70B) instantly analyzes the unstructured text to determine the severity (P1/P2/P3), category, and risk tier.
- **Why it matters for demo:** Showcases the speed of the Groq API and how the system immediately understands incidents without requiring pre-trained, rigid classification models.

### 2. Semantic Root Cause Analysis (RCA) Engine
- **What it does:** Uses a local sentence-transformer (`all-MiniLM-L6-v2`) and a FAISS vector database to perform a blazing-fast semantic search over 46,000+ historical IT incidents. 
- **The Magic:** Instead of just guessing, it retrieves the Top-3 most similar past incidents and feeds them to the LLM. The AI then synthesizes a plain-English root cause and generates a strict 3-5 step actionable fix based *only* on proven historical precedents.

### 3. Adaptive Human-in-the-Loop Governance (Dynamic Routing)
- **What it does:** The system doesn't execute blindly; it intelligently limits its own autonomy based on calculated risk.
  - **Path A (Auto-Execute):** High-confidence, low-risk fixes trigger a 10-second countdown (cancel window) before executing autonomously.
  - **Path B (Operator Approval):** Mid-confidence fixes pause, requiring a human operator to click "Approve" or "Reject".
  - **Path C (Mandatory Senior Review):** Critical issues (like P1 outages) or low-confidence guesses are hard-gated, requiring textual justification from a senior engineer before proceeding.

### 4. Continuous Learning & Trust Calibration
- **What it does:** The system gets smarter without needing to be retrained. It tracks `fix_outcomes`. When humans approve an AI-recommended fix, the AI's confidence score for that category organically increases in the future.
- **Instant Memory:** If a fix is rolled back or rejected, the system learns and penalizes that fix. Furthermore, every newly resolved ticket is immediately embedded and appended to the FAISS index, instantly updating the AI's "memory."

### 5. Full Traceability & The Audit Trail
- **What it does:** "Every AI decision is traceable, auditable, and reversible." Providing a real-time Audit Trail screen that logs all actions: predictions, overrides, approvals, and autonomous executions.
- **Why it matters:** Captures pre-state and post-state snapshots, operator IDs, and confidence scores, proving to the judges that the system is fully compliant and safe for enterprise deployment.

---

## ✨ Minor but Differentiating Features (The "Smart Details")

These details separate TamePND from typical wrapper apps and show deep engineering thought. Mention these during technical deep-dives or as quick callouts.

### 1. Deterministic Confidence Jitter
- **The Feature:** Instead of the LLM returning a robotic, repetitive "55%" confidence score for similar tickets, the backend applies a deterministic mathematical hash jitter (-3 to +3). 
- **Why it's cool:** It prevents score clustering, making the AI's confidence scoring feel highly organic, realistic, and sensitive to minute contextual differences while remaining mathematically consistent.

### 2. Offline-First Vector Search
- **The Feature:** Rather than relying on costly API calls for embeddings (like OpenAI embeddings), the platform uses local, highly optimized MiniLM embeddings.
- **Why it's cool:** The 46,606 ticket vectors are searched entirely locally via FAISS in milliseconds. This highlights a focus on data privacy (no sensitive incident data sent to embedding APIs), zero cost, and extreme speed.

### 3. Automated Triage Fallback
- **The Feature:** AI isn't perfect. If the Groq RCA generation fails, times out, or hallucinates, the engine detects this and seamlessly falls back to a deterministic 5-step IT triage guide.
- **Why it's cool:** Demonstrates robust fault tolerance. The UI never breaks or shows an error dump; operations continue smoothly even if the LLM provider experiences an outage.

### 4. Ultra-Lean "Dependency-Free" React Architecture
- **The Feature:** The entire complex Governance Dashboard is built using an incredibly lean, almost dependency-free architecture.
- **Why it's cool:** Contained almost entirely in a single file (`App.jsx`), it uses pure CSS-in-JS instead of bulky libraries like Tailwind or Material-UI. It achieves a stunning, premium dark-theme aesthetic with smooth micro-animations (like the Path A 10-second ring loader) in under 1000 lines of code.

---

## 🎬 Suggested Demo Video Flow (3-Minute Script)

1. **Ingest a Major Incident:** Open the dashboard and show the 46k tickets loaded. Use the floating action button to inject a "P1 DB Outage" preset.
2. **Watch the RCA:** Click the new ticket. Show the glowing RCA panel loading the Top-3 similar incidents and generating the 3-5 step fix.
3. **Show Governance:** Click "Proceed to Approval." Point out that because it's a P1, the AI has safely routed it to **Path C (Senior Review)**. Type a justification and execute.
4. **Demonstrate Auto-Execute:** Inject a low-risk P3 ticket. Show it landing in **Path A (Auto-Execute)** and let the 10-second countdown ring expire while the system fixes it autonomously.
5. **The Proof:** Switch over to the Audit Trail. Show the detailed logs of what just happened, emphasizing the "Enterprise-Ready Safety" aspect.
