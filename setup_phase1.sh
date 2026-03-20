#!/bin/bash
# =============================================================================
#  OpsAI — Phase 1 Setup Script
#  Run this once to set up the entire Phase 1 environment.
#  Usage: bash setup_phase1.sh
# =============================================================================

set -e  # exit on any error

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════╗"
echo "║          OpsAI — Phase 1 Setup                  ║"
echo "║  Human-Governed Autonomous AI Support            ║"
echo "╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Step 1: Check Python ──────────────────────────────────────────────────────
echo -e "${YELLOW}[1/6] Checking Python version...${NC}"
python_version=$(python --version 2>&1 | awk '{print $2}')
echo -e "      Python: $python_version"
if python -c "import sys; exit(0 if sys.version_info >= (3,9) else 1)"; then
    echo -e "      ${GREEN}✅ Python 3.9+ confirmed${NC}"
else
    echo -e "      ${RED}❌ Python 3.9+ required. Install from python.org${NC}"
    exit 1
fi

# ── Step 2: Create virtual environment ───────────────────────────────────────
echo -e "\n${YELLOW}[2/6] Setting up virtual environment...${NC}"
if [ ! -d "venv" ]; then
    python -m venv venv
    echo -e "      ${GREEN}✅ venv created${NC}"
else
    echo -e "      ℹ️  venv already exists, skipping"
fi

source venv/Scripts/activate
echo -e "      ${GREEN}✅ venv activated${NC}"

# ── Step 3: Install dependencies ─────────────────────────────────────────────
echo -e "\n${YELLOW}[3/6] Installing dependencies...${NC}"
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo -e "      ${GREEN}✅ All packages installed${NC}"

# ── Step 4: Setup .env ────────────────────────────────────────────────────────
echo -e "\n${YELLOW}[4/6] Setting up environment file...${NC}"
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo -e "      ${GREEN}✅ .env created from template${NC}"
    echo -e "      ${YELLOW}⚠️  Remember to add your GROQ_API_KEY to .env before Phase 2${NC}"
else
    echo -e "      ℹ️  .env already exists, skipping"
fi

# ── Step 5: Generate + normalize data ────────────────────────────────────────
echo -e "\n${YELLOW}[5/6] Setting up data...${NC}"
mkdir -p data db

if [ -f "ITSM_data.csv" ]; then
    echo -e "      📂 Found ITSM_data.csv — normalizing..."
    python normalize_dataset.py \
        --input ITSM_data.csv \
        --output data/tickets_clean.csv
    echo -e "      ${GREEN}✅ ITSM_data dataset normalized${NC}"
else
    echo -e "      ℹ️  No ITSM_data dataset found — generating synthetic tickets..."
    python generate_tickets.py \
        --count 150 \
        --output data/tickets_raw.csv

    python normalize_dataset.py \
        --input data/tickets_raw.csv \
        --output data/tickets_clean.csv
    echo -e "      ${GREEN}✅ 150 synthetic tickets generated and normalized${NC}"
fi

# Create demo feed (20 tickets with resolution notes stripped)
python - <<'PYEOF'
import csv, random

with open("data/tickets_clean.csv") as f:
    rows = list(csv.DictReader(f))

# Pick 5 P1, 8 P2, 7 P3
p1 = [r for r in rows if r["severity"] == "P1"][:5]
p2 = [r for r in rows if r["severity"] == "P2"][:8]
p3 = [r for r in rows if r["severity"] == "P3"][:7]
demo = p1 + p2 + p3
random.shuffle(demo)

# Strip resolution so system must figure it out
for r in demo:
    r["resolution_notes"] = ""
    r["status"] = "open"

if demo:
    with open("data/demo_tickets.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=demo[0].keys())
        writer.writeheader()
        writer.writerows(demo)
    print(f"      Created demo_tickets.csv with {len(demo)} tickets (P1:{len(p1)}, P2:{len(p2)}, P3:{len(p3)})")
PYEOF
echo -e "      ${GREEN}✅ demo_tickets.csv ready${NC}"

# ── Step 6: Setup database ────────────────────────────────────────────────────
echo -e "\n${YELLOW}[6/6] Setting up database...${NC}"
python setup_db.py --data data/tickets_clean.csv
echo -e "      ${GREEN}✅ Database ready${NC}"

# ── Done! ─────────────────────────────────────────────────────────────────────
echo -e "\n${GREEN}"
echo "╔══════════════════════════════════════════════════╗"
echo "║        ✅ Phase 1 Setup Complete!                ║"
echo "╚══════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "📁 Project structure:"
echo -e "   data/tickets_clean.csv   — ${BLUE}full dataset (memory + seeding)${NC}"
echo -e "   data/demo_tickets.csv    — ${BLUE}20 tickets for live demo${NC}"
echo -e "   db/opsai.db              — ${BLUE}SQLite database (seeded)${NC}"
echo ""
echo -e "🚀 Start the API server:"
echo -e "   ${YELLOW}source venv/Scripts/activate${NC}"
echo -e "   ${YELLOW}uvicorn ingestion:app --reload --port 8000${NC}"
echo ""
echo -e "🧪 Test the API:"
echo -e "   ${YELLOW}curl http://localhost:8000/health${NC}"
echo -e "   ${YELLOW}curl http://localhost:8000/stats${NC}"
echo -e "   ${YELLOW}curl http://localhost:8000/tickets${NC}"
echo ""
echo -e "🎬 Run live demo feed (after server is up):"
echo -e "   ${YELLOW}python demo_feed.py --input data/demo_tickets.csv --interval 3${NC}"
echo ""
echo -e "📖 API docs: ${BLUE}http://localhost:8000/docs${NC}"
echo ""
echo -e "${YELLOW}⚠️  Next: Add GROQ_API_KEY to .env, then proceed to Phase 2${NC}"
