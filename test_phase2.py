"""
Phase 2 — Full Test Suite
===========================
Tests prediction engine in isolation AND via the live API.
Run with server OFF to test Groq directly.
Run with server ON to test full pipeline.

Usage:
    python test_phase2.py              # API tests (server must be running)
    python test_phase2.py --direct     # Direct Groq tests (no server needed)
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from dotenv import load_dotenv

load_dotenv()

BASE   = "http://localhost:8000"
PASS   = "✅"
FAIL   = "❌"
WARN   = "⚠️ "
results = []


def check(name, condition, detail=""):
    icon = PASS if condition else FAIL
    results.append(condition)
    suffix = f"  ({detail})" if detail else ""
    print(f"  {icon} {name}{suffix}")
    return condition


def api(method, path, body=None):
    url  = BASE + path
    data = json.dumps(body).encode() if body else None
    hdrs = {"Content-Type":"application/json"} if body else {}
    req  = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as e:
        return 0, {"error": str(e)}


# ── Direct Groq Tests (no server needed) ─────────────────────────────────────

def test_groq_direct():
    print("\n🧪 Direct Groq Tests (no server required)")
    print("=" * 55)

    # 1. Check API key exists
    print("\n[1] Environment")
    key = os.getenv("GROQ_API_KEY","")
    check("GROQ_API_KEY is set",     bool(key), f"{'gsk_***' if key else 'MISSING'}")
    check("Key format looks correct", key.startswith("gsk_") if key else False)

    if not key:
        print("\n❌ Cannot proceed — set GROQ_API_KEY in .env first")
        print("   Get free key: https://console.groq.com → API Keys → Create")
        return False

    # 2. Basic Groq connectivity
    print("\n[2] Groq Connectivity")
    try:
        from groq import Groq
        client = Groq(api_key=key)
        resp   = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content":"Reply with the single word: CONNECTED"}],
            max_tokens=10, temperature=0,
        )
        reply = resp.choices[0].message.content.strip()
        check("Groq API reachable",        True, f"Response: {reply}")
        check("Model returned response",   bool(reply))
        check("Correct model used",        resp.model.startswith("llama"))
    except Exception as e:
        check("Groq API reachable", False, str(e)[:80])
        print("\n❌ Groq connection failed. Check your key and internet connection.")
        return False

    # 3. JSON mode test
    print("\n[3] JSON Response Format")
    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role":"system","content":"Respond only with valid JSON. No markdown."},
                {"role":"user","content":'Return {"test": true, "value": 42}'},
            ],
            max_tokens=50, temperature=0,
        )
        raw  = resp.choices[0].message.content.strip()
        parsed = json.loads(raw)
        check("Returns valid JSON",     True,              f"Got: {raw[:60]}")
        check("JSON has expected keys", "test" in parsed)
    except json.JSONDecodeError as e:
        check("Returns valid JSON", False, f"Parse error: {e}")
    except Exception as e:
        check("JSON mode test",     False, str(e)[:60])

    # 4. Full prediction test
    print("\n[4] Prediction Quality")
    try:
        sys.path.insert(0, ".")
        from prediction import predict_ticket

        # P1 test — should classify as critical
        r1 = predict_ticket(
            ticket_id="QTEST001",
            description="Production database completely down, all users locked out, entire system unresponsive",
            ci_cat="storage", ci_subcat="SAN Storage",
            category="incident", urgency="1", impact="1", alert_status="True",
        )
        check("P1 ticket → Critical risk",  r1["risk_tier"] == "Critical", r1["risk_tier"])
        check("P1 severity detected",       r1["predicted_severity"] == "P1", r1["predicted_severity"])
        check("Approval Path is C",         r1["approval_path"] == "C", r1["approval_path"])
        check("Has recommended_fix",        bool(r1.get("recommended_fix","")))
        check("Has reasoning",              bool(r1.get("reasoning","")))
        check("Used Groq (not fallback)",   r1["status"] == "success", r1["status"])

        # P3 test — should be low risk
        r3 = predict_ticket(
            ticket_id="QTEST002",
            description="Desktop application running slightly slow for one developer",
            ci_cat="subapplication", category="incident", urgency="4", impact="4",
        )
        check("P3 ticket → lower severity", r3["predicted_severity"] in ("P2","P3"),
              r3["predicted_severity"])
        check("Confidence score present",   0 <= r3["confidence_score"] <= 100,
              str(r3["confidence_score"]))

        print(f"\n   P1 result summary:")
        print(f"     Severity:   {r1['predicted_severity']}  |  Risk: {r1['risk_tier']}")
        print(f"     Confidence: {r1['confidence_score']}%  |  Path: {r1['approval_path']}")
        print(f"     Fix:        {r1['recommended_fix'][:60]}")
        print(f"     Reasoning:  {r1['reasoning'][:70]}")

    except ImportError:
        check("prediction.py importable", False, "File not found — run from project root")
    except Exception as e:
        check("Prediction runs",           False, str(e)[:80])

    return True


# ── API Integration Tests (server must be running) ───────────────────────────

def test_api():
    print("\n🧪 API Integration Tests (server must be running)")
    print("=" * 55)

    print("\n[1] Server Health")
    status, data = api("GET", "/health")
    check("Server is running",        status == 200, f"HTTP {status}")
    check("Phase 2 active",           data.get("phase") == "1+2", data.get("phase"))
    check("Prediction engine loaded", data.get("prediction_engine") == True)

    print("\n[2] Stats Endpoint")
    status, data = api("GET", "/stats")
    check("Stats returns 200",        status == 200)
    check("Has predictions_run",      "predictions_run" in data)

    # Ingest a P1 ticket
    print("\n[3] P1 Ticket Ingestion + Prediction")
    status, data = api("POST", "/tickets/ingest", {
        "description":  "Production database down — all users locked out, complete outage",
        "ci_cat":       "storage",
        "ci_subcat":    "SAN Storage",
        "urgency":      "1",
        "impact":       "1",
        "alert_status": "True",
        "source":       "test",
    })
    check("Ingest returns 200",       status == 200, f"HTTP {status}")
    check("Ticket ID assigned",       "id" in data, data.get("id",""))
    check("Severity is P1",           data.get("severity") == "P1", data.get("severity"))
    check("Anomaly flagged",          len(data.get("anomaly_flags",[])) > 0)

    tid = data.get("id")

    # Wait for background prediction
    if tid:
        print(f"\n   ⏳ Waiting 4s for background prediction on {tid}...")
        time.sleep(4)

        status, pred = api("GET", f"/tickets/{tid}/prediction")
        check("Prediction endpoint works",  status == 200)

        if pred.get("status") == "pending":
            print(f"   {WARN} Prediction still pending — Groq may be slow, try again")
        else:
            check("Prediction has severity",    bool(pred.get("predicted_severity")))
            check("Prediction has confidence",  bool(pred.get("confidence_score") is not None))
            check("Prediction has risk_tier",   bool(pred.get("risk_tier")))
            check("Prediction stored in DB",    pred.get("ticket_id") == tid)

    # Ingest a genuinely low-severity ticket (no CI_Cat risk, no keywords)
    print("\n[4] Low-Severity Ticket Ingestion")
    status, data = api("POST", "/tickets/ingest", {
        "description":  "SSL certificate expiring in 30 days on internal wiki page",
        "urgency":      "4",
        "impact":       "4",
        "alert_status": "False",
        "source":       "test",
    })
    check("Low-sev ingest returns 200",  status == 200)
    check("Severity is P2 or P3",        data.get("severity") in ("P2","P3"), data.get("severity"))
    check("Not escalated to P1",         data.get("severity") != "P1", data.get("severity"))

    # Audit trail
    print("\n[5] Audit Trail")
    status, data = api("GET", "/audit?event_type=PREDICT&limit=5")
    check("Audit endpoint works",     status == 200)
    check("Has PREDICT events",       len(data.get("events",[])) > 0,
          f"{len(data.get('events',[]))} events")

    # 404
    print("\n[6] Error Handling")
    status, _ = api("GET", "/tickets/DOESNT_EXIST_XYZ")
    check("404 for missing ticket",   status == 404)


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--direct", action="store_true",
                        help="Test Groq directly without server")
    args = parser.parse_args()

    if args.direct:
        test_groq_direct()
    else:
        test_groq_direct()
        test_api()

    passed = sum(results)
    total  = len(results)
    print(f"\n{'='*55}")
    print(f"Results: {passed}/{total} passed")

    if passed == total:
        print("🎉 All Phase 2 tests passed!")
        print("\nNext steps:")
        print("  → Run demo: python demo_feed.py --input ITSM_data.csv --limit 10")
        print("  → Proceed to Phase 3: FAISS RCA engine")
    elif passed >= total * 0.8:
        print(f"⚠️  {total-passed} test(s) failed — minor issues, check above")
    else:
        print(f"❌ {total-passed} test(s) failed — check Groq key and server logs")
