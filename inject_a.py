import requests
import json
import time

URL = "http://127.0.0.1:8000/tickets/ingest"

ticket_data = {
    "description": "Minor cosmetic UI issue on internal employee portal. No user impact. Suggested fix: reload css assets for employee-dev-box-01.",
    "severity": "P3",
    "category": "UI",
}

print(f"Ingesting Path A ticket...")
response = requests.post(URL, json=ticket_data)

if response.status_code == 200:
    res = response.json()
    ticket_id = res.get("id")
    print(f"SUCCESS: Ticket {ticket_id} ingested.")
    print("Waiting 15 seconds for RCA and Auto-Approve to complete...")
    time.sleep(15)
    
    # Check status
    try:
        status_url = f"http://127.0.0.1:8000/tickets/{ticket_id}"
        st_res = requests.get(status_url).json()
        print(f"Final Status: {st_res.get('status')} | Assigned: {st_res.get('assigned_group')}")
        
        # Check audit
        audit_url = f"http://127.0.0.1:8000/tickets/{ticket_id}/audit"
        au_res = requests.get(audit_url).json()
        if au_res and "audit_trail" in au_res:
             for ev in au_res["audit_trail"]:
                if ev['event_type'] == "AUTO_APPROVE":
                    print(f"✅ AUTO-APPROVE confirmed: {ev['reasoning']}")
                    print(f"Notes: {st_res.get('resolution_notes')}")
    except Exception as e:
        print(f"DEBUG Error: {e}")
else:
    print(f"FAILED: {response.status_code} - {response.text}")
