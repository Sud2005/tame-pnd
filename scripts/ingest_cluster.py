import httpx
import time

URL = "http://localhost:8000/tickets/ingest"

descriptions = [
    "Database connection timeout on production master node DB-01",
    "Timeout error when connecting to production database DB-01",
    "Cannot connect to DB-01 production database connection timeout",
    "DB-01 production database is unresponsive and timing out",
    "Connection to production DB-01 failing with timeout exception",
    "Database timeout on DB-01 prod instance"
]

def main():
    for desc in descriptions:
        payload = {
            "description": desc,
            "ci_cat": "application",
            "urgency": "1",
            "impact": "1",
            "alert_status": "True",
            "source": "monitoring"
        }
        try:
            r = httpx.post(URL, json=payload, timeout=10.0)
            print(f"Sent: {desc[:30]}... Response: {r.status_code}")
        except Exception as e:
            print(f"Failed: {e}")
        time.sleep(1)

if __name__ == "__main__":
    main()
