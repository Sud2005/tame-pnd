"""
Phase 1 — Demo Feed Script
============================
Feeds tickets from demo_tickets.csv into the live API one by one.
Run this during your demo to simulate real-time ticket ingestion.

Usage:
    # Feed tickets with 3-second gaps (good for live demo)
    python demo_feed.py --input data/demo_tickets.csv --interval 3

    # Feed all at once (for setup)
    python demo_feed.py --input data/demo_tickets.csv --interval 0

    # Feed only P1 tickets (dramatic demo)
    python demo_feed.py --input data/demo_tickets.csv --severity P1
"""

import argparse
import csv
import json
import time
import urllib.request
import urllib.error

API_BASE = "http://127.0.0.1:8000"


def post_ticket(ticket: dict) -> dict:
    payload = json.dumps({
        "description":    ticket.get("description", ""),
        "severity":       ticket.get("severity") or None,
        "category":       ticket.get("category") or None,
        "assigned_group": ticket.get("assigned_group") or None,
        "source":         "csv_feed",
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{API_BASE}/tickets/ingest",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()}
    except Exception as e:
        return {"error": str(e)}


def check_api():
    try:
        with urllib.request.urlopen(f"{API_BASE}/health", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def prepare_demo_tickets(csv_path: str, severity_filter=None) -> list:
    """Load and strip resolution notes — system must figure it out fresh."""
    tickets = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Extract priority/severity handling different column names and cases
            raw_priority = str(row.get("severity") or row.get("Severity") or row.get("priority") or row.get("Priority") or "").strip()
            priority = f"P{raw_priority}" if raw_priority in ["1", "2", "3", "4", "5"] else raw_priority
            
            if severity_filter and priority != severity_filter:
                continue
                
            desc = row.get("description") or row.get("short_description") or row.get("CI_Name") or "No description provided"
            
            tickets.append({
                "description":    desc,
                "severity":       priority if priority else None,
                "category":       row.get("Category") or row.get("category") or None,
                "assigned_group": row.get("assigned_group") or row.get("Assignment_Group") or "",
            })
    return tickets


def main():
    global API_BASE
    parser = argparse.ArgumentParser(description="Demo ticket feed")
    parser.add_argument("--input",    default="ITSM_data.csv")
    parser.add_argument("--interval", type=float, default=3.0,
                        help="Seconds between tickets (0 for instant)")
    parser.add_argument("--severity", default=None,
                        help="Filter by severity: P1, P2, P3")
    parser.add_argument("--limit",    type=int, default=20,
                        help="Max tickets to feed")
    parser.add_argument("--api",      default=API_BASE)
    args = parser.parse_args()

    API_BASE = args.api

    # Check API is running
    print(f"\n🔌 Checking API at {API_BASE}...")
    if not check_api():
        print("❌ API not reachable. Start with: uvicorn ingestion:app --reload")
        return

    print("✅ API is live\n")

    # Load tickets
    tickets = prepare_demo_tickets(args.input, args.severity)
    tickets = tickets[:args.limit]

    if not tickets:
        print(f"❌ No tickets found matching filter (severity={args.severity})")
        return

    print(f"📋 Feeding {len(tickets)} tickets")
    if args.severity:
        print(f"   Filter: severity={args.severity}")
    print(f"   Interval: {args.interval}s between each")
    print(f"{'─'*55}\n")

    success: int = 0
    failed: int = 0

    for i, ticket in enumerate(tickets, 1):
        desc_preview = ticket["description"][:55] + "..." if len(ticket["description"]) > 55 else ticket["description"]
        print(f"[{i:02d}/{len(tickets)}] Ingesting: {desc_preview}")

        result = post_ticket(ticket)

        if "error" in result:
            print(f"        ❌ Error: {result['error']}")
            failed += 1  # type: ignore
        else:
            sev   = result.get("severity", "?")
            cat   = result.get("category", "?")
            flags = result.get("anomaly_flags", [])
            tid   = result.get("id", "?")

            sev_icon = {"P1": "🔴", "P2": "🟡", "P3": "🟢"}.get(sev, "⚪")
            flag_str  = f" ⚠️  {flags[0]}" if flags else ""

            print(f"        ✅ {tid} | {sev_icon} {sev} | {cat}{flag_str}")
            success += 1  # type: ignore

        if args.interval > 0 and i < len(tickets):
            time.sleep(args.interval)

    print(f"\n{'─'*55}")
    print(f"✅ Done. {success} ingested, {failed} failed.")
    print(f"\nView in dashboard: http://localhost:3000")
    print(f"Or via API:        {API_BASE}/tickets")


if __name__ == "__main__":
    main()
