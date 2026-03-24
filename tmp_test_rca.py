import requests
import json
import time

def test_fallback():
    # 1. Ingest
    res = requests.post('http://127.0.0.1:8000/tickets/ingest', json={'description': 'Triggering RCA test case 4', 'severity': 'P2'})
    ticket_id = res.json()['id']
    print(f"Created ticket: {ticket_id}")
    
    # Wait for RCA to start
    time.sleep(1)
    
    # 2. Get RCA
    res2 = requests.get(f'http://127.0.0.1:8000/tickets/{ticket_id}/rca/result')
    print("RCA Result:")
    print(json.dumps(res2.json(), indent=2))

if __name__ == "__main__":
    test_fallback()
