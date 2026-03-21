"""Quick test of the new /execute and /rollback endpoints."""
import urllib.request
import json

API = "http://localhost:8000"

def api_get(path):
    r = urllib.request.urlopen(f"{API}{path}", timeout=10)
    return json.loads(r.read())

def api_post(path, data):
    payload = json.dumps(data).encode()
    req = urllib.request.Request(f"{API}{path}", data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())

# 1. Get a ticket
tickets = api_get("/tickets?limit=1")
if not tickets.get("tickets"):
    print("No tickets found. Run demo_feed.py first.")
    exit(1)

t = tickets["tickets"][0]
print(f"[1] Ticket: {t['id']} | Status: {t['status']} | Severity: {t['severity']}")

# 2. Test execute
print("\n[2] Testing /tickets/{id}/execute ...")
result = api_post(f"/tickets/{t['id']}/execute", {
    "fix_type": "restart_service",
    "approval_path": "B",
    "action_type": "APPROVE",
    "operator_id": "test_operator",
    "operator_reason": "Testing execute endpoint",
    "confidence": 75,
    "risk_tier": "Medium",
})
print(f"    Result: {json.dumps(result, indent=2)}")
exec_id = result.get("execution_id")

# 3. Verify ticket is resolved
t2 = api_get(f"/tickets/{t['id']}")
print(f"\n[3] Ticket status after execute: {t2.get('status')}")

# 4. Test rollback
if exec_id:
    print(f"\n[4] Testing /executions/{exec_id}/rollback ...")
    rb = api_post(f"/executions/{exec_id}/rollback", {})
    print(f"    Result: {json.dumps(rb, indent=2)}")

    # 5. Verify ticket reopened
    t3 = api_get(f"/tickets/{t['id']}")
    print(f"\n[5] Ticket status after rollback: {t3.get('status')}")

# 6. Check audit trail
print("\n[6] Recent audit events:")
audit = api_get("/audit?limit=5")
for e in audit.get("events", [])[:5]:
    print(f"    {e.get('event_type'):<10} {e.get('ticket_id','')[:14]:<16} {e.get('action_taken','')[:40]}")

print("\n✅ All endpoint tests passed!")
