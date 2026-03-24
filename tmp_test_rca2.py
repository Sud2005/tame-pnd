import requests
import json
import time

def test_fallback():
    # 1. Ingest
    print("Ingesting ticket...")
    res = requests.post('http://127.0.0.1:8000/tickets/ingest', json={'description': 'Triggering RCA test case 7 - db cluster down', 'severity': 'P1'})
    ticket_id = res.json()['id']
    print(f"Created ticket: {ticket_id}")
    
    # Wait for RCA to start
    for i in range(15):
        time.sleep(2)
        res2 = requests.get(f'http://127.0.0.1:8000/tickets/{ticket_id}/rca/result')
        data = res2.json()
        if data.get('status') == 'success':
            print("RCA Result:")
            print(json.dumps(data.get('fix_steps', []), indent=2))
            return
        elif data.get('status') == 'fallback':
            print("Fallback triggered!")
            print(json.dumps(data.get('fix_steps', []), indent=2))
            return
        print("Waiting...")

if __name__ == "__main__":
    test_fallback()
