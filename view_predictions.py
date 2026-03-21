"""
Live Prediction Viewer
=======================
Ingests test tickets and displays all prediction fields in real time.
No server needed for --direct mode. Server needed for --api mode.

Usage:
    python view_predictions.py            # uses live API
    python view_predictions.py --direct   # calls Groq directly, no server
"""

import argparse
import json
import time
import urllib.request
import urllib.error
import os
from dotenv import load_dotenv

load_dotenv()

BASE = "http://localhost:8000"

# ── Test tickets that show the full range of predictions ─────────────────────

TEST_TICKETS = [
    {
        "label":        "🔴 P1 — Database Outage",
        "description":  "Production database completely unresponsive, all users locked out of the system",
        "ci_cat":       "storage",
        "ci_subcat":    "SAN Storage",
        "urgency":      "1",
        "impact":       "1",
        "alert_status": "True",
    },
    {
        "label":        "🔴 P1 — Security Breach",
        "description":  "Unauthorized access detected on payment processing server, possible data breach",
        "ci_cat":       "application",
        "ci_subcat":    "Web Based Application",
        "urgency":      "1",
        "impact":       "1",
        "alert_status": "True",
    },
    {
        "label":        "🟡 P2 — Network Degradation",
        "description":  "Intermittent packet loss on payment network segment, some transactions timing out",
        "ci_cat":       "network",
        "ci_subcat":    "Network Infrastructure",
        "urgency":      "2",
        "impact":       "2",
        "alert_status": "False",
    },
    {
        "label":        "🟡 P2 — App Performance",
        "description":  "Web based application responding slowly, report generation timing out for large queries",
        "ci_cat":       "subapplication",
        "ci_subcat":    "Web Based Application",
        "urgency":      "3",
        "impact":       "2",
        "alert_status": "False",
    },
    {
        "label":        "🟢 P3 — Certificate Expiry",
        "description":  "SSL certificate expiring in 14 days on internal monitoring dashboard",
        "urgency":      "4",
        "impact":       "4",
        "alert_status": "False",
    },
]


def print_divider(char="─", width=60):
    print(char * width)


def print_prediction(label: str, prediction: dict, keyword_flags: list = None):
    """Pretty-print all prediction fields."""
    sev   = prediction.get("predicted_severity") or prediction.get("severity", "?")
    cat   = prediction.get("predicted_category") or prediction.get("category", "?")
    inc   = prediction.get("predicted_incident_type", "?")
    anom  = prediction.get("anomaly_flagged", False)
    conf  = prediction.get("confidence_score", "?")
    risk  = prediction.get("risk_tier", "?")
    fix   = prediction.get("recommended_fix", "?")
    rsn   = prediction.get("reasoning", "?")
    path  = prediction.get("approval_path", "?")
    model = prediction.get("model_used", "?")
    flags = keyword_flags or prediction.get("keyword_flags", [])

    sev_icon  = {"P1": "🔴", "P2": "🟡", "P3": "🟢"}.get(sev, "⚪")
    risk_icon = {"Critical": "🚨", "Medium": "⚠️ ", "Low": "✅"}.get(risk, "❓")
    path_label = {
        "A": "🟢 Auto-Execute (10s cancel window)",
        "B": "🟡 Single Operator Approval",
        "C": "🔴 Mandatory Senior Review",
    }.get(path, path)
    anom_str  = "⚠️  YES" if anom else "✅ No"

    print_divider("═")
    print(f"  {label}")
    print_divider("─")
    print(f"  {'severity':<22}  {sev_icon}  {sev}")
    print(f"  {'category':<22}  📁  {cat}")
    print(f"  {'predicted_incident_type':<22}  🎯  {inc}")
    print(f"  {'anomaly_flagged':<22}  {anom_str}")
    print(f"  {'confidence_score':<22}  📊  {conf}%")
    print(f"  {'risk_tier':<22}  {risk_icon}  {risk}")
    print_divider("─")
    print(f"  {'recommended_fix':<22}  🔧  {fix}")
    print_divider("─")
    print(f"  {'reasoning':<22}  💬  {rsn[:70]}{'...' if len(str(rsn)) > 70 else ''}")
    print_divider("─")
    print(f"  {'approval_path':<22}  🛤️   {path_label}")
    if flags:
        print(f"  {'keyword_flags':<22}  🚩  {', '.join(flags)}")
    print(f"  {'model_used':<22}  🤖  {model}")
    print()


def run_direct():
    """Call prediction engine directly without the server."""
    print("\n🔍 Direct Prediction Viewer (Groq API, no server)")
    print_divider("═")

    try:
        from prediction import predict_ticket
    except ImportError:
        print("❌ prediction.py not found. Run from your project folder.")
        return

    if not os.getenv("GROQ_API_KEY"):
        print("❌ GROQ_API_KEY not set in .env")
        return

    for i, ticket in enumerate(TEST_TICKETS, 1):
        label = ticket.pop("label")
        desc  = ticket.pop("description")

        print(f"\n  Sending ticket {i}/{len(TEST_TICKETS)}: {label}")
        print(f"  Description: \"{desc[:65]}...\"" if len(desc) > 65 else f"  Description: \"{desc}\"")
        print("  ⏳ Calling Groq...\n")

        try:
            result = predict_ticket(
                ticket_id=f"VIEW{i:03d}",
                description=desc,
                **ticket
            )
            print_prediction(label, result, result.get("keyword_flags", []))
        except Exception as e:
            print(f"  ❌ Failed: {e}\n")

        if i < len(TEST_TICKETS):
            print("  Waiting 1s before next ticket...\n")
            time.sleep(1)

    print_divider("═")
    print("✅ Done. All predictions shown above.")


def run_api():
    """Ingest via API, wait, then fetch and display prediction."""
    print("\n🌐 API Prediction Viewer (live server)")
    print_divider("═")

    # Check server
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=3) as r:
            health = json.loads(r.read())
        phase = health.get("phase", "?")
        pred  = health.get("prediction_engine", False)
        print(f"  ✅ Server running | Phase: {phase} | Prediction: {'ON' if pred else 'OFF'}\n")
    except Exception:
        print("  ❌ Server not running. Start with:")
        print("     uvicorn ingestion:app --reload --port 8000")
        print("  Or use: python view_predictions.py --direct")
        return

    for i, ticket in enumerate(TEST_TICKETS, 1):
        label = ticket.pop("label")
        desc  = ticket.pop("description")

        print(f"  [{i}/{len(TEST_TICKETS)}] {label}")
        print(f"  Description: \"{desc[:65]}\"")

        # Ingest
        payload = json.dumps({"description": desc, "source": "viewer", **ticket}).encode()
        req     = urllib.request.Request(
            f"{BASE}/tickets/ingest", data=payload,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                ingest = json.loads(r.read())
        except Exception as e:
            print(f"  ❌ Ingest failed: {e}\n")
            continue

        tid   = ingest.get("id", "")
        flags = ingest.get("anomaly_flags", [])
        print(f"  ✅ Ingested as {tid} | Keyword severity: {ingest.get('severity','?')}")
        print(f"  ⏳ Waiting 4s for Groq prediction...\n")
        time.sleep(4)

        # Fetch prediction
        try:
            with urllib.request.urlopen(f"{BASE}/tickets/{tid}/prediction", timeout=5) as r:
                pred = json.loads(r.read())
        except Exception as e:
            print(f"  ❌ Prediction fetch failed: {e}\n")
            continue

        if pred.get("status") == "pending":
            print("  ⏳ Still processing — waiting 3 more seconds...\n")
            time.sleep(3)
            try:
                with urllib.request.urlopen(f"{BASE}/tickets/{tid}/prediction", timeout=5) as r:
                    pred = json.loads(r.read())
            except Exception:
                pass

        if pred.get("status") == "pending":
            print("  ⚠️  Prediction not ready yet. Check server logs.\n")
        else:
            # Map DB column names to display names
            display = {
                "predicted_severity":      pred.get("predicted_severity"),
                "predicted_category":      pred.get("predicted_category"),
                "predicted_incident_type": pred.get("predicted_incident"),
                "anomaly_flagged":         bool(pred.get("anomaly_flagged", 0)),
                "confidence_score":        pred.get("confidence_score"),
                "risk_tier":               pred.get("risk_tier"),
                "recommended_fix":         pred.get("reasoning","")[:60],
                "reasoning":               pred.get("reasoning",""),
                "approval_path":           "C" if pred.get("risk_tier")=="Critical" else
                                           "A" if (pred.get("confidence_score") or 0) >= 85 else "B",
                "model_used":              "llama-3.3-70b-versatile",
            }
            print_prediction(label, display, flags)

        if i < len(TEST_TICKETS):
            time.sleep(1)

    print_divider("═")
    print("✅ All predictions displayed.")
    print(f"\nFull audit trail: {BASE}/audit")
    print(f"All predictions:  {BASE}/audit?event_type=PREDICT")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="View live predictions")
    parser.add_argument("--direct", action="store_true",
                        help="Call Groq directly without server")
    args = parser.parse_args()

    if args.direct:
        run_direct()
    else:
        run_api()
