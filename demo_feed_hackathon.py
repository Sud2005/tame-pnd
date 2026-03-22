"""
Hackathon Demo Feed
====================
Sends exactly 4 P1, 4 P2, 4 P3 tickets designed to hit all three
approval paths:
  P1 → Path C (mandatory senior review)
  P2 → Path B (single operator approval)
  P3 → Path A (auto-execute)

Each description is carefully written to:
  - Give Groq real signal for accurate classification
  - NOT accidentally trigger keyword escalation beyond intended severity
  - Produce varied confidence scores (not all 55%)

Usage:
    python demo_feed_hackathon.py                    # all 12 tickets, 4s gap
    python demo_feed_hackathon.py --severity P1      # only P1s
    python demo_feed_hackathon.py --interval 6       # slower for live demo
    python demo_feed_hackathon.py --interval 0       # instant (setup mode)
"""

import argparse
import json
import time
import urllib.request
import urllib.error

API_BASE = "http://127.0.0.1:8000"

# ── Carefully balanced demo tickets ──────────────────────────────────────────
# P1: Rich context, high urgency/impact → Groq gives high confidence → Path C
# P2: Clear degradation, partial impact → Medium confidence → Path B
# P3: Routine/scheduled, no urgency → High confidence, low risk → Path A

DEMO_TICKETS = [
    # ── P1 — Path C (Mandatory Senior Review) ─────────────────────────────
    {
        "description": "SAN storage array reporting hardware fault, database writes failing across cluster",
        "severity": "P1",
        "ci_cat": "storage",
        "ci_subcat": "SAN Storage",
        "urgency": "1",
        "impact": "1",
        "alert_status": "True",
        "source": "monitoring",
        "_label": "P1 — Storage Hardware Fault",
    },
    {
        "description": "Core network switch failed in datacenter, multiple services unreachable",
        "severity": "P1",
        "ci_cat": "network",
        "ci_subcat": "Network Infrastructure",
        "urgency": "1",
        "impact": "1",
        "alert_status": "True",
        "source": "monitoring",
        "_label": "P1 — Network Switch Failure",
    },
    {
        "description": "Authentication service returning 500 errors, login requests rejected for all tenants",
        "severity": "P1",
        "ci_cat": "application",
        "ci_subcat": "Web Based Application",
        "urgency": "1",
        "impact": "1",
        "alert_status": "True",
        "source": "monitoring",
        "_label": "P1 — Auth Service Down",
    },
    {
        "description": "Payment processing API returning gateway errors, transactions failing since 10 minutes",
        "severity": "P1",
        "ci_cat": "subapplication",
        "ci_subcat": "Server Based Application",
        "urgency": "1",
        "impact": "1",
        "alert_status": "True",
        "source": "monitoring",
        "_label": "P1 — Payment Gateway Error",
    },

    # ── P2 — Path B (Single Operator Approval) ────────────────────────────
    {
        "description": "Web application response times elevated to 8 seconds, subset of users affected",
        "severity": "P2",
        "ci_cat": "subapplication",
        "ci_subcat": "Web Based Application",
        "urgency": "2",
        "impact": "2",
        "alert_status": "False",
        "source": "monitoring",
        "_label": "P2 — App Performance Degraded",
    },
    {
        "description": "Database replication lag increasing on secondary node, reads becoming stale",
        "severity": "P2",
        "ci_cat": "storage",
        "ci_subcat": "SAN Storage",
        "urgency": "2",
        "impact": "3",
        "alert_status": "False",
        "source": "helpdesk",
        "_label": "P2 — DB Replication Lag",
    },
    {
        "description": "VPN connections dropping intermittently for remote office users in Mumbai region",
        "severity": "P2",
        "ci_cat": "network",
        "ci_subcat": "Network Infrastructure",
        "urgency": "2",
        "impact": "2",
        "alert_status": "False",
        "source": "helpdesk",
        "_label": "P2 — VPN Intermittent Drop",
    },
    {
        "description": "Memory utilization on application server reaching 85 percent, response times increasing",
        "severity": "P2",
        "ci_cat": "application",
        "ci_subcat": "Server Based Application",
        "urgency": "3",
        "impact": "2",
        "alert_status": "False",
        "source": "monitoring",
        "_label": "P2 — Server Memory Pressure",
    },

    # ── P3 — Path A (Auto-Execute) ────────────────────────────────────────
    {
        "description": "SSL certificate on internal reporting dashboard expiring in 14 days, renewal needed",
        "severity": "P3",
        "ci_cat": "",
        "ci_subcat": "Desktop Application",
        "urgency": "4",
        "impact": "4",
        "alert_status": "False",
        "source": "scheduled",
        "_label": "P3 — Certificate Renewal Due",
    },
    {
        "description": "Scheduled disk cleanup job failed on dev server, non-critical log rotation not completed",
        "severity": "P3",
        "ci_cat": "subapplication",
        "ci_subcat": "Desktop Application",
        "urgency": "4",
        "impact": "4",
        "alert_status": "False",
        "source": "scheduler",
        "_label": "P3 — Disk Cleanup Job Failed",
    },
    {
        "description": "Report generation taking 45 seconds for large date ranges in analytics module",
        "severity": "P3",
        "ci_cat": "application",
        "ci_subcat": "Web Based Application",
        "urgency": "4",
        "impact": "3",
        "alert_status": "False",
        "source": "helpdesk",
        "_label": "P3 — Slow Report Generation",
    },
    {
        "description": "Password reset email delivery delayed by 5 minutes, SMTP queue backlog detected",
        "severity": "P3",
        "ci_cat": "application",
        "ci_subcat": "Web Based Application",
        "urgency": "3",
        "impact": "4",
        "alert_status": "False",
        "source": "helpdesk",
        "_label": "P3 — Email Delivery Delay",
    },
]


def post_ticket(ticket: dict) -> dict:
    # Remove internal label before sending
    payload = {k: v for k, v in ticket.items() if not k.startswith("_")}
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        f"{API_BASE}/tickets/ingest",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()}
    except Exception as e:
        return {"error": str(e)}


def check_api():
    try:
        with urllib.request.urlopen(f"{API_BASE}/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    global API_BASE
    parser = argparse.ArgumentParser(description="Hackathon demo ticket feed")
    parser.add_argument("--severity", default=None,  help="P1, P2, or P3 only")
    parser.add_argument("--interval", type=float, default=4.0)
    parser.add_argument("--api",      default=API_BASE)
    args = parser.parse_args()
    API_BASE = args.api

    print(f"\n🔌 Checking API at {API_BASE}...")
    if not check_api():
        print("❌ API not reachable. Start with: uvicorn ingestion:app --reload --port 8000")
        return
    print("✅ API is live\n")

    tickets = DEMO_TICKETS
    if args.severity:
        tickets = [t for t in tickets if t["severity"] == args.severity.upper()]
        if not tickets:
            print(f"❌ No tickets found for severity={args.severity}")
            return

    # Show what will be sent
    p1 = sum(1 for t in tickets if t["severity"] == "P1")
    p2 = sum(1 for t in tickets if t["severity"] == "P2")
    p3 = sum(1 for t in tickets if t["severity"] == "P3")
    print(f"📋 Sending {len(tickets)} tickets  |  🔴 P1:{p1}  🟡 P2:{p2}  🟢 P3:{p3}")
    print(f"   Expected paths: P1→Path C  |  P2→Path B  |  P3→Path A")
    print(f"   Interval: {args.interval}s between tickets")
    print(f"{'─'*60}\n")

    success = 0
    failed  = 0

    for i, ticket in enumerate(tickets, 1):
        label   = ticket["_label"]
        sev     = ticket["severity"]
        sev_icon = {"P1": "🔴", "P2": "🟡", "P3": "🟢"}[sev]
        path_exp = {"P1": "→ Path C", "P2": "→ Path B", "P3": "→ Path A"}[sev]

        print(f"[{i:02d}/{len(tickets)}] {sev_icon} {label}  {path_exp}")

        result = post_ticket(ticket)

        if "error" in result:
            print(f"         ❌ Error: {result['error']}")
            failed += 1
        else:
            tid      = result.get("id", "?")
            got_sev  = result.get("severity", "?")
            flags    = result.get("anomaly_flags", [])
            flag_str = f"  ⚠ {flags[0]}" if flags else ""

            # Warn if severity was changed by keyword engine
            sev_match = "✅" if got_sev == sev else f"⚠️  escalated to {got_sev}"

            print(f"         ✅ {tid}  |  Stored: {got_sev} {sev_match}{flag_str}")
            success += 1

        if args.interval > 0 and i < len(tickets):
            time.sleep(args.interval)

    print(f"\n{'─'*60}")
    print(f"Done: {success} ingested, {failed} failed")
    print(f"\nCheck dashboard: http://localhost:3000")
    print(f"P1 tickets → click any → Path C (senior review required)")
    print(f"P2 tickets → click any → Path B (one-click approve/reject)")
    print(f"P3 tickets → click any → Path A (auto-execute countdown)")


if __name__ == "__main__":
    main()
