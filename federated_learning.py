"""
Phase 5 — Cross-Organisation Federated Learning (Simulated)
=============================================================
Multiple organisations share fix outcome signals — not raw data — across a
federated network. This allows any single org to benefit from the collective
experience of the entire network.

How it works conceptually:
  Organisation A fixes a DB connection pool issue → success
  Organisation B fixes a similar issue → failure, rolled back
  Organisation C hasn't seen this yet

  Federated signal: "DB connection pool + increase pool size = 0.73 success
  rate across 3 orgs"

  Organisation C's confidence: now informed by cross-org history

Implementation: Deterministic mock — seeded data simulating 5 organisations.
In production: gRPC/REST mesh between org instances, with differential
privacy guarantees on shared signals.
"""

import hashlib
import random
from datetime import datetime, timedelta


# ── Simulated Organisations ──────────────────────────────────────────────────

ORGS = [
    {
        "id": "atos_eu",
        "name": "Atos EU",
        "region": "Europe",
        "ticket_volume": 46606,
        "joined": "2024-01-15",
        "status": "active",
    },
    {
        "id": "atos_apac",
        "name": "Atos APAC",
        "region": "Asia-Pacific",
        "ticket_volume": 31240,
        "joined": "2024-03-08",
        "status": "active",
    },
    {
        "id": "techcorp_global",
        "name": "TechCorp Global",
        "region": "North America",
        "ticket_volume": 52890,
        "joined": "2024-02-22",
        "status": "active",
    },
    {
        "id": "finserv_intl",
        "name": "FinServ International",
        "region": "Europe",
        "ticket_volume": 28710,
        "joined": "2024-04-11",
        "status": "active",
    },
    {
        "id": "healthnet_corp",
        "name": "HealthNet Corp",
        "region": "North America",
        "ticket_volume": 19450,
        "joined": "2024-05-30",
        "status": "active",
    },
]

# ── Seeded fix outcome signals — per org, per category ───────────────────────
# Format: { (org_id, category): { fix_type: (success, fail, rollback) } }
# These are deterministic seeds — same data every time.

_FEDERATED_SIGNALS = {
    # Atos EU
    ("atos_eu", "Database"): {
        "restart_db_service":    (142, 18, 5),
        "clear_connection_pool": (98,  12, 3),
        "increase_pool_size":    (67,   5, 1),
        "add_missing_index":     (89,   8, 2),
    },
    ("atos_eu", "Network"): {
        "restart_network_service": (115, 14, 4),
        "update_firewall_rules":   (78,  22, 7),
        "failover_to_secondary":   (45,   3, 0),
    },
    ("atos_eu", "Authentication"): {
        "restart_sso_service":      (92, 11, 3),
        "rotate_service_account":   (108,  6, 1),
        "sync_active_directory":    (64,  15, 4),
    },
    ("atos_eu", "Infrastructure"): {
        "restart_application":  (156, 19, 6),
        "clear_disk_space":     (121,  5, 0),
        "scale_horizontal":     (48,   8, 2),
    },
    ("atos_eu", "Application"): {
        "rollback_deployment":   (87,  4, 1),
        "restart_app_service":   (134, 16, 5),
        "update_config":         (95,  12, 3),
    },
    ("atos_eu", "General"): {
        "restart_service":  (45, 8, 2),
        "escalate_to_engineer": (62, 5, 0),
    },

    # Atos APAC
    ("atos_apac", "Database"): {
        "restart_db_service":    (98, 14, 4),
        "clear_connection_pool": (76, 10, 2),
        "increase_pool_size":    (52,  7, 3),
    },
    ("atos_apac", "Network"): {
        "restart_network_service": (88, 11, 3),
        "update_firewall_rules":   (55, 18, 6),
    },
    ("atos_apac", "Authentication"): {
        "restart_sso_service":    (71,  9, 2),
        "rotate_service_account": (82,  5, 1),
    },
    ("atos_apac", "Infrastructure"): {
        "restart_application": (112, 15, 4),
        "clear_disk_space":    (89,   3, 0),
    },
    ("atos_apac", "Application"): {
        "rollback_deployment": (65,  6, 2),
        "restart_app_service": (98, 12, 3),
    },
    ("atos_apac", "General"): {
        "restart_service": (35, 6, 1),
    },

    # TechCorp Global
    ("techcorp_global", "Database"): {
        "restart_db_service":    (178, 22, 7),
        "clear_connection_pool": (124, 15, 4),
        "increase_pool_size":    (89,   4, 0),
    },
    ("techcorp_global", "Network"): {
        "restart_network_service": (145, 18, 5),
        "failover_to_secondary":   (67,   2, 0),
    },
    ("techcorp_global", "Authentication"): {
        "restart_sso_service":    (105, 13, 4),
        "sync_active_directory":  (78,  20, 6),
    },
    ("techcorp_global", "Infrastructure"): {
        "restart_application": (189, 24, 8),
        "scale_horizontal":    (72,  10, 3),
    },
    ("techcorp_global", "Application"): {
        "rollback_deployment":  (112,  8, 2),
        "restart_app_service":  (167, 21, 6),
        "update_config":        (118, 14, 4),
    },
    ("techcorp_global", "General"): {
        "restart_service":      (58, 10, 3),
        "escalate_to_engineer": (84,  7, 0),
    },

    # FinServ International
    ("finserv_intl", "Database"): {
        "restart_db_service":    (82, 10, 3),
        "clear_connection_pool": (64,  8, 2),
    },
    ("finserv_intl", "Network"): {
        "update_firewall_rules": (48, 14, 5),
    },
    ("finserv_intl", "Authentication"): {
        "rotate_service_account": (91,  4, 0),
    },
    ("finserv_intl", "Infrastructure"): {
        "restart_application": (95, 12, 4),
        "clear_disk_space":    (72,   2, 0),
    },
    ("finserv_intl", "Application"): {
        "restart_app_service": (88, 10, 3),
    },
    ("finserv_intl", "General"): {
        "restart_service": (28, 4, 1),
    },

    # HealthNet Corp
    ("healthnet_corp", "Database"): {
        "restart_db_service":    (54, 7, 2),
        "increase_pool_size":    (38, 3, 1),
    },
    ("healthnet_corp", "Network"): {
        "restart_network_service": (62, 8, 2),
    },
    ("healthnet_corp", "Authentication"): {
        "restart_sso_service": (48, 6, 1),
    },
    ("healthnet_corp", "Infrastructure"): {
        "restart_application": (71, 9, 3),
    },
    ("healthnet_corp", "Application"): {
        "rollback_deployment": (42, 5, 1),
        "restart_app_service": (58, 7, 2),
    },
    ("healthnet_corp", "General"): {
        "restart_service": (22, 3, 0),
    },
}


def get_federated_confidence_boost(category: str, fix_type: str = None) -> dict:
    """
    Compute a confidence boost from federated cross-org signals.

    Aggregates fix outcome data across all organisations for the given
    category (and optionally fix_type), and returns the cross-org success
    rate and confidence adjustment.

    Returns:
        {
            "boost": int,                  # confidence adjustment (-8 to +12)
            "cross_org_success_rate": float,# 0.0-1.0
            "contributing_orgs": int,       # how many orgs have data
            "total_cross_org_fixes": int,   # total fix actions across network
            "org_signals": [...],           # per-org breakdown
            "signal_strength": str,         # "strong" / "moderate" / "weak"
        }
    """
    total_success = 0
    total_fail = 0
    total_rollback = 0
    org_signals = []

    for (org_id, cat), fixes in _FEDERATED_SIGNALS.items():
        if cat != category:
            continue

        org_success = 0
        org_fail = 0
        org_rollback = 0

        for ft, (s, f, r) in fixes.items():
            if fix_type and ft != fix_type:
                continue
            org_success += s
            org_fail += f
            org_rollback += r

        org_total = org_success + org_fail + org_rollback
        if org_total == 0:
            continue

        org_info = next((o for o in ORGS if o["id"] == org_id), None)
        org_rate = org_success / org_total

        org_signals.append({
            "org_id": org_id,
            "org_name": org_info["name"] if org_info else org_id,
            "region": org_info["region"] if org_info else "Unknown",
            "success_count": org_success,
            "fail_count": org_fail,
            "rollback_count": org_rollback,
            "total": org_total,
            "success_rate": round(org_rate, 3),
        })

        total_success += org_success
        total_fail += org_fail
        total_rollback += org_rollback

    total_fixes = total_success + total_fail + total_rollback
    contributing_orgs = len(org_signals)

    if total_fixes == 0 or contributing_orgs == 0:
        return {
            "boost": 0,
            "cross_org_success_rate": 0.0,
            "contributing_orgs": 0,
            "total_cross_org_fixes": 0,
            "org_signals": [],
            "signal_strength": "none",
        }

    cross_org_rate = total_success / total_fixes

    # Calculate boost:
    # - Success rate > 80% → positive boost (+4 to +12)
    # - Success rate 60-80% → small boost (+1 to +4)
    # - Success rate < 60% → negative adjustment (-3 to -8)
    # - More contributing orgs = stronger signal
    org_weight = min(contributing_orgs / 3.0, 1.0)  # saturates at 3 orgs

    if cross_org_rate >= 0.80:
        raw_boost = 4 + int((cross_org_rate - 0.80) * 40)  # 4 to 12
    elif cross_org_rate >= 0.60:
        raw_boost = 1 + int((cross_org_rate - 0.60) * 15)  # 1 to 4
    else:
        raw_boost = -3 - int((0.60 - cross_org_rate) * 12)  # -3 to -8

    boost = int(raw_boost * org_weight)
    boost = max(-8, min(12, boost))

    # Signal strength
    if contributing_orgs >= 4 and total_fixes >= 200:
        strength = "strong"
    elif contributing_orgs >= 2 and total_fixes >= 50:
        strength = "moderate"
    else:
        strength = "weak"

    return {
        "boost": boost,
        "cross_org_success_rate": round(cross_org_rate, 3),
        "contributing_orgs": contributing_orgs,
        "total_cross_org_fixes": total_fixes,
        "org_signals": org_signals,
        "signal_strength": strength,
    }


def get_federated_network_stats() -> dict:
    """
    Returns aggregate statistics about the entire federated network.
    Used by the dashboard Insights > Federated tab.
    """
    total_orgs = len(ORGS)
    total_volume = sum(o["ticket_volume"] for o in ORGS)

    # Aggregate all signals
    category_stats = {}
    for (org_id, cat), fixes in _FEDERATED_SIGNALS.items():
        if cat not in category_stats:
            category_stats[cat] = {
                "success": 0, "fail": 0, "rollback": 0,
                "orgs": set(),
            }
        for ft, (s, f, r) in fixes.items():
            category_stats[cat]["success"] += s
            category_stats[cat]["fail"] += f
            category_stats[cat]["rollback"] += r
            category_stats[cat]["orgs"].add(org_id)

    categories = []
    total_signals = 0
    for cat, data in sorted(category_stats.items()):
        total = data["success"] + data["fail"] + data["rollback"]
        total_signals += total
        rate = data["success"] / total if total > 0 else 0
        categories.append({
            "category": cat,
            "success_rate": round(rate, 3),
            "total_fixes": total,
            "contributing_orgs": len(data["orgs"]),
            "success_count": data["success"],
            "fail_count": data["fail"],
            "rollback_count": data["rollback"],
        })

    # Simulate recent activity
    rng = random.Random(42)
    recent_syncs = []
    now = datetime.now()
    for i in range(8):
        org = ORGS[rng.randint(0, len(ORGS) - 1)]
        minutes_ago = rng.randint(5, 180)
        cat = rng.choice(list(category_stats.keys()))
        recent_syncs.append({
            "org_name": org["name"],
            "region": org["region"],
            "category": cat,
            "signal_type": rng.choice(["success", "success", "success", "rollback"]),
            "synced_at": (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S"),
        })
    recent_syncs.sort(key=lambda x: x["synced_at"], reverse=True)

    return {
        "network_name": "TamePND Federated Network",
        "protocol": "Federated Signal Sharing v1.0",
        "privacy": "Differential Privacy — no raw data shared",
        "total_organisations": total_orgs,
        "active_organisations": sum(1 for o in ORGS if o["status"] == "active"),
        "total_ticket_volume": total_volume,
        "total_shared_signals": total_signals,
        "organisations": ORGS,
        "category_breakdown": categories,
        "recent_syncs": recent_syncs,
        "network_health": "healthy",
        "last_sync": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\nPhase 5 -- Cross-Organisation Federated Learning")
    print("=" * 55)

    print("\n[1] Network Stats:")
    stats = get_federated_network_stats()
    print(f"    Orgs: {stats['total_organisations']}")
    print(f"    Total ticket volume: {stats['total_ticket_volume']:,}")
    print(f"    Total shared signals: {stats['total_shared_signals']:,}")

    print("\n[2] Category breakdown:")
    for cat in stats["category_breakdown"]:
        print(f"    {cat['category']:<20} {cat['success_rate']:.1%} success "
              f"({cat['total_fixes']} fixes, {cat['contributing_orgs']} orgs)")

    print("\n[3] Federated confidence boosts:")
    for cat in ["Database", "Network", "Authentication", "Infrastructure", "Application"]:
        signal = get_federated_confidence_boost(cat)
        print(f"    {cat:<20} boost={signal['boost']:+d}  "
              f"rate={signal['cross_org_success_rate']:.1%}  "
              f"orgs={signal['contributing_orgs']}  "
              f"strength={signal['signal_strength']}")
