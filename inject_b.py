import requests
import uuid
import time
from datetime import datetime, timezone

API_URL = "http://127.0.0.1:8000/tickets/ingest"

# We craft tickets that are likely to get Path B
# Path B logic:
# P2 with confidence 50-84
# P3 with confidence 40-69

tickets_to_inject = [
    {
        "description": "Secondary internal search pod is experiencing intermittent latency spikes (around 200ms). The primary pod is handling traffic fine, but the secondary needs investigation to ensure no capacity degradation during peak hours later today.",
        "category": "Infrastructure",
        "severity": "P2" # P2 with moderate urgency, likely confidence ~70-80 -> Path B
    },
    {
        "description": "An employee noted that the internal HR portal is loading slightly slower than usual when accessing archived PDF documents. No error messages are shown, but it takes 5-10 seconds to render.",
        "category": "Application",
        "severity": "P3" # P3 but very ambiguous root cause, so confidence likely <70 -> Path B
    },
    {
        "description": "Log sync from the EU-West application cluster to the central reporting bucket is delayed by about 15 minutes. This doesn't affect user transactions but slows down hourly metrics dashboards.",
        "category": "Data",
        "severity": "P2" # P2 with low immediate impact, confidence likely ~75-80 -> Path B
    }
]

for t in tickets_to_inject:
    ticket_data = {
        "description": t["description"],
        "category": t["category"],
        "severity": t["severity"]
    }
    
    try:
        r = requests.post(API_URL, json=ticket_data)
        if r.status_code == 200:
            resp_data = r.json()
            print(f"✅ Injected: {resp_data.get('id', 'Unknown ID')} ({t['severity']})")
        else:
            print(f"❌ Failed to inject: {r.text}")
    except Exception as e:
        print(f"❌ Connection error: {e}")
        
    time.sleep(1) # small pause

print("Injection complete. Check the LIVE FEED on the dashboard in a few seconds!")
