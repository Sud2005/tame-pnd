"""
Phase 1 — API Test Suite
==========================
Verifies every Phase 1 endpoint is working correctly.
Run AFTER the server is started.

Usage:
    python test_phase1.py
"""

import json
import urllib.request
import urllib.error

BASE = "http://localhost:8000"
PASS = "✅"
FAIL = "❌"
results = []


def req(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as e:
        return 0, {"error": str(e)}


def test(name, condition, detail=""):
    icon = PASS if condition else FAIL
    results.append(condition)
    detail_str = f"  ({detail})" if detail else ""
    print(f"  {icon} {name}{detail_str}")


print("\n🧪 OpsAI Phase 1 — API Tests")
print("=" * 45)

# ── Health check ──────────────────────────────────────────────
print("\n[1] Health Check")
status, data = req("GET", "/health")
test("API is running",       status == 200)
test("Returns status: ok",   data.get("status") == "ok", data.get("status"))

# ── Stats ─────────────────────────────────────────────────────
print("\n[2] Statistics")
status, data = req("GET", "/stats")
test("Stats endpoint works",         status == 200)
test("Has total_tickets",            "total_tickets" in data, str(data.get("total_tickets")))
test("Has audit_events",             "audit_events" in data,  str(data.get("audit_events")))
test("Database was seeded",          data.get("total_tickets", 0) > 0,
     f"{data.get('total_tickets', 0)} tickets in DB")

# ── Ticket listing ────────────────────────────────────────────
print("\n[3] Ticket Listing")
status, data = req("GET", "/tickets?limit=5")
test("List tickets works",   status == 200)
test("Returns ticket array", isinstance(data.get("tickets"), list))
test("Has total count",      "total" in data, str(data.get("total")))

# ── Ticket ingestion ──────────────────────────────────────────
print("\n[4] Ticket Ingestion — Normal P3")
status, data = req("POST", "/tickets/ingest", {
    "description": "Dev server running slowly, developers reporting lag.",
    "source": "test"
})
test("Ingestion returns 200",     status == 200)
test("Returns ticket ID",         "id" in data, data.get("id", ""))
test("Detected as P3",            data.get("severity") == "P3", data.get("severity"))
test("Category detected",         data.get("category") not in (None, ""), data.get("category"))
test("No anomaly flagged",        len(data.get("anomaly_flags", [])) == 0)

ingested_id = data.get("id")

# ── P1 auto-detection ─────────────────────────────────────────
print("\n[5] Ticket Ingestion — P1 Auto-Detection")
status, data = req("POST", "/tickets/ingest", {
    "description": "Production database unresponsive — all users locked out, complete outage.",
    "source": "test"
})
test("Ingestion returns 200",     status == 200)
test("Escalated to P1",           data.get("severity") == "P1", data.get("severity"))
test("Anomaly was flagged",       len(data.get("anomaly_flags", [])) > 0,
     str(data.get("anomaly_flags", [])))
test("Category is Database",      data.get("category") == "Database", data.get("category"))

# ── Single ticket fetch ───────────────────────────────────────
print("\n[6] Single Ticket Fetch")
if ingested_id:
    status, data = req("GET", f"/tickets/{ingested_id}")
    test("Fetch by ID works",         status == 200)
    test("ID matches",                data.get("id") == ingested_id)
    test("Status is open",            data.get("status") == "open")

# ── Audit trail ───────────────────────────────────────────────
print("\n[7] Audit Trail")
status, data = req("GET", "/audit?limit=10")
test("Audit endpoint works",      status == 200)
test("Has events array",          isinstance(data.get("events"), list))
test("Has audit entries",         len(data.get("events", [])) > 0,
     f"{len(data.get('events', []))} entries")

if ingested_id:
    status, data = req("GET", f"/tickets/{ingested_id}/audit")
    test("Per-ticket audit works",    status == 200)
    test("Has INGEST event",
         any(e["event_type"] == "INGEST" for e in data.get("events", [])))

# ── Filters ───────────────────────────────────────────────────
print("\n[8] Filters")
status, data = req("GET", "/tickets?severity=P1&limit=5")
test("Severity filter works",     status == 200)
test("All results are P1",        all(t["severity"] == "P1" for t in data.get("tickets", [])))

# ── 404 handling ──────────────────────────────────────────────
print("\n[9] Error Handling")
status, data = req("GET", "/tickets/NONEXISTENT_ID_XYZ")
test("Returns 404 for missing ticket", status == 404)

# ── Summary ───────────────────────────────────────────────────
passed = sum(results)
total  = len(results)
print(f"\n{'='*45}")
print(f"Results: {passed}/{total} tests passed")

if passed == total:
    print("🎉 All tests passed! Phase 1 is fully operational.")
    print("\nNext steps:")
    print("  → Run demo feed:  python demo_feed.py --input data/demo_tickets.csv")
    print("  → Add Groq key to .env")
    print("  → Proceed to Phase 2 (Prediction Engine)")
else:
    failed = total - passed
    print(f"⚠️  {failed} test(s) failed. Check server logs.")
    print("   Is the server running? uvicorn ingestion:app --reload")
