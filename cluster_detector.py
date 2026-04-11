"""
Phase 4 — Predictive Incident Clustering
==========================================
Detects emerging incident patterns by clustering recent tickets
using DBSCAN on sentence-transformer embeddings.

When 3+ tickets cluster in the same semantic space within a 2-hour
window, the system raises a predictive alert — catching outages
before they're flagged as P1.

Architecture:
  1. Pull tickets ingested in the last 2 hours
  2. Embed all descriptions using the same model as RCA
  3. Run DBSCAN clustering (eps=0.3, min_samples=3)
  4. For each meaningful cluster → write to incident_clusters table
  5. Return detected clusters for the dashboard

Run standalone: python cluster_detector.py
"""

import json
import sqlite3
import uuid
import threading
import time
from datetime import datetime, timedelta

import numpy as np

DB_PATH = "db/opsai.db"

# Clustering parameters — tuned for normalized MiniLM-L6 embeddings
DBSCAN_EPS         = 0.3     # cosine distance threshold (1 - similarity)
DBSCAN_MIN_SAMPLES = 3       # minimum tickets to form a cluster
LOOKBACK_HOURS     = 2       # how far back to pull tickets
MAX_TIME_SPAN_MIN  = 120     # cluster must span ≤ this many minutes
SCAN_INTERVAL_SEC  = 900     # 15 minutes between scans

_scanner_thread = None
_scanner_running = False


def predict_incident_clusters(force: bool = False) -> list[dict]:
    """
    Main clustering pipeline.

    1. Pull recent tickets (last 2 hours)
    2. Embed descriptions
    3. DBSCAN clustering
    4. Filter clusters by time span
    5. Persist new clusters to DB
    6. Return list of detected clusters

    Args:
        force: If True, skip duplicate detection and always write new clusters

    Returns:
        List of cluster dicts with ticket details
    """
    from rca_engine import embed_batch

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    cutoff = (datetime.now() - timedelta(hours=LOOKBACK_HOURS)).strftime("%Y-%m-%d %H:%M:%S")

    # Pull recent tickets — exclude resolved ones (they're handled)
    rows = conn.execute("""
        SELECT id, description, severity, category, opened_at, status
        FROM   tickets
        WHERE  opened_at >= ?
          AND  status NOT IN ('resolved', 'user_cancelled')
          AND  description IS NOT NULL
          AND  description != ''
        ORDER  BY opened_at DESC
        LIMIT  500
    """, (cutoff,)).fetchall()

    if len(rows) < DBSCAN_MIN_SAMPLES:
        conn.close()
        return []

    tickets = [dict(r) for r in rows]
    descriptions = [t["description"] for t in tickets]

    # Embed all descriptions
    try:
        vectors = embed_batch(descriptions)
    except Exception as e:
        print(f"⚠️  Cluster embedding failed: {e}")
        conn.close()
        return []

    # DBSCAN clustering — uses cosine distance (1 - dot product on normalized vectors)
    from sklearn.cluster import DBSCAN

    # Since embeddings are L2-normalized, cosine distance = 1 - dot_product
    # We convert to distance matrix for DBSCAN
    similarity_matrix = np.dot(vectors, vectors.T)
    distance_matrix = 1.0 - similarity_matrix
    np.fill_diagonal(distance_matrix, 0)  # zero self-distance
    distance_matrix = np.clip(distance_matrix, 0, 2)  # ensure non-negative

    labels = DBSCAN(
        eps=DBSCAN_EPS,
        min_samples=DBSCAN_MIN_SAMPLES,
        metric="precomputed",
    ).fit_predict(distance_matrix)

    # Group tickets by cluster label (ignore noise label -1)
    from collections import defaultdict
    clusters_map = defaultdict(list)
    for i, label in enumerate(labels):
        if label == -1:
            continue
        clusters_map[label].append(tickets[i])

    # Filter and build cluster objects
    detected = []
    for label, cluster_tickets in clusters_map.items():
        if len(cluster_tickets) < DBSCAN_MIN_SAMPLES:
            continue

        # Calculate time span of the cluster
        times = []
        for t in cluster_tickets:
            ts = t.get("opened_at") or t.get("created_at", "")
            if ts:
                try:
                    times.append(datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S"))
                except ValueError:
                    pass

        if len(times) < 2:
            continue

        time_span = (max(times) - min(times)).total_seconds() / 60.0

        if time_span > MAX_TIME_SPAN_MIN:
            continue

        # Get representative description (most central ticket)
        centroid_desc = _get_representative_description(cluster_tickets)
        severity = _suggest_cluster_severity(cluster_tickets)
        ticket_ids = [t["id"] for t in cluster_tickets]

        # Check for duplicate clusters — don't re-create if a cluster
        # with the same tickets already exists in 'active' state
        if not force:
            existing = conn.execute("""
                SELECT id, ticket_ids FROM incident_clusters
                WHERE status = 'active' AND detected_at >= ?
            """, (cutoff,)).fetchall()

            is_duplicate = False
            for ex in existing:
                try:
                    ex_ids = set(json.loads(ex["ticket_ids"]))
                    new_ids = set(ticket_ids)
                    # If >60% overlap, treat as duplicate
                    overlap = len(ex_ids & new_ids) / max(len(ex_ids | new_ids), 1)
                    if overlap > 0.6:
                        is_duplicate = True
                        break
                except Exception:
                    pass

            if is_duplicate:
                continue

        cluster_id = str(uuid.uuid4())
        cluster_label = _generate_cluster_label(cluster_tickets)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn.execute("""
            INSERT INTO incident_clusters
            (id, cluster_label, ticket_ids, ticket_count,
             time_span_minutes, centroid_description,
             severity_suggestion, status, detected_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            cluster_id, cluster_label,
            json.dumps(ticket_ids), len(cluster_tickets),
            round(time_span, 1), centroid_desc,
            severity, "active", now,
        ))

        detected.append({
            "id":                   cluster_id,
            "cluster_label":        cluster_label,
            "ticket_ids":           ticket_ids,
            "ticket_count":         len(cluster_tickets),
            "time_span_minutes":    round(time_span, 1),
            "centroid_description": centroid_desc,
            "severity_suggestion":  severity,
            "status":               "active",
            "detected_at":          now,
            "tickets":              cluster_tickets,
        })

    if detected:
        conn.commit()
        print(f"🔮 Detected {len(detected)} incident cluster(s)")
        for c in detected:
            print(f"   → [{c['severity_suggestion']}] {c['cluster_label']} "
                  f"({c['ticket_count']} tickets, {c['time_span_minutes']}min span)")

    conn.close()
    return detected


def _get_representative_description(tickets: list[dict]) -> str:
    """Pick the longest description as representative (most informative)."""
    if not tickets:
        return "Unknown cluster"
    best = max(tickets, key=lambda t: len(t.get("description", "")))
    return (best.get("description") or "Unknown")[:200]


def _suggest_cluster_severity(tickets: list[dict]) -> str:
    """Highest severity ticket in cluster determines the cluster's severity."""
    rank = {"P1": 1, "P2": 2, "P3": 3}
    best = min(tickets, key=lambda t: rank.get(t.get("severity", "P3"), 3))
    return best.get("severity", "P2")


def _generate_cluster_label(tickets: list[dict]) -> str:
    """Generate a human-readable label from the cluster's common keywords."""
    from collections import Counter

    # Collect all words from descriptions, filter out stop words
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "for", "of", "in", "on", "to", "and", "or", "not", "with",
        "from", "at", "by", "that", "this", "it", "its", "all",
        "has", "have", "had", "will", "can", "may", "should",
        "would", "could", "being", "having", "do", "does", "did",
        "no", "up", "out", "if", "about", "which", "when", "after",
    }

    words = Counter()
    for t in tickets:
        desc = (t.get("description") or "").lower()
        for word in desc.split():
            clean = word.strip(".,;:!?()-\"'")
            if len(clean) > 2 and clean not in stop_words:
                words[clean] += 1

    # Top 3 most common words → label
    top = [w for w, _ in words.most_common(4)]
    if not top:
        categories = set(t.get("category", "General") for t in tickets)
        return f"Cluster — {', '.join(categories)}"

    return " ".join(top[:3]).title()


# ── Background scanner ────────────────────────────────────────────────────────

def start_background_scanner():
    """Launch a daemon thread that scans for clusters every 15 minutes."""
    global _scanner_thread, _scanner_running

    if _scanner_running:
        return

    _scanner_running = True

    def _scan_loop():
        # Wait 60s after startup before first scan (let index warm up)
        time.sleep(60)
        while _scanner_running:
            try:
                predict_incident_clusters()
            except Exception as e:
                print(f"⚠️  Cluster scan error: {e}")
            time.sleep(SCAN_INTERVAL_SEC)

    _scanner_thread = threading.Thread(target=_scan_loop, daemon=True, name="cluster-scanner")
    _scanner_thread.start()
    print("🔮 Background cluster scanner started (every 15 min)")


def stop_background_scanner():
    """Stop the background scanner thread."""
    global _scanner_running
    _scanner_running = False


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🧪 Phase 4 — Predictive Incident Clustering Test")
    print("=" * 55)

    print("\n[1] Running cluster detection on recent tickets...")
    try:
        clusters = predict_incident_clusters(force=True)
        if clusters:
            print(f"\n✅ Detected {len(clusters)} cluster(s):")
            for c in clusters:
                print(f"\n   Cluster: {c['cluster_label']}")
                print(f"   Severity: {c['severity_suggestion']}")
                print(f"   Tickets: {c['ticket_count']}")
                print(f"   Time span: {c['time_span_minutes']} minutes")
                print(f"   Representative: {c['centroid_description'][:80]}...")
                print(f"   Ticket IDs: {c['ticket_ids'][:5]}{'...' if len(c['ticket_ids']) > 5 else ''}")
        else:
            print("   No clusters detected (need 3+ similar tickets within 2 hours)")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 55)
    print("✅ Phase 4 test complete")
