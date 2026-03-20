"""
Phase 1 — Synthetic Ticket Generator
=====================================
Run this if Kaggle dataset isn't available OR to supplement it.
Generates realistic ITSM tickets with full schema.

Usage:
    python scripts/generate_tickets.py --count 100 --output data/tickets_raw.csv
"""

import argparse
import csv
import random
import uuid
from datetime import datetime, timedelta

# ─── Realistic ticket templates per category ───────────────────────────────

TEMPLATES = {
    "Database": {
        "P1": [
            ("Production database unresponsive, all services down",
             "Restarted DB service, cleared connection pool, verified replica sync"),
            ("DB replication lag exceeding 30 seconds on primary",
             "Tuned replication buffer, restarted slave thread, lag normalized"),
            ("ORA-12541: TNS no listener error on prod DB cluster",
             "Restarted TNS listener, updated tnsnames.ora, connections restored"),
        ],
        "P2": [
            ("Slow query degrading application performance, CPU at 94%",
             "Identified missing index on orders table, added index, query time dropped from 12s to 0.3s"),
            ("Database connection pool exhausted during peak hours",
             "Increased max pool size from 100 to 250, added connection timeout handling"),
            ("Deadlock detected on inventory update transactions",
             "Identified circular lock pattern, refactored transaction order in application code"),
        ],
        "P3": [
            ("Scheduled DB backup job failed silently last night",
             "Fixed cron expression, verified backup completed successfully, added alerting"),
            ("Non-critical reporting DB showing stale data by 2 hours",
             "Restarted ETL pipeline, data refreshed, root cause was memory pressure on ETL server"),
        ],
    },
    "Network": {
        "P1": [
            ("Complete network outage in datacenter zone B, 200+ users affected",
             "Identified failed core switch, activated failover route, traffic restored in 18 min"),
            ("VPN gateway unresponsive, remote workers cannot connect",
             "Restarted VPN service on gateway, cleared stale sessions, connectivity restored"),
            ("DNS resolution failing for all internal services",
             "Primary DNS server crashed, failed over to secondary, primary rebuilt"),
        ],
        "P2": [
            ("Intermittent packet loss on payment processing network segment",
             "Replaced faulty SFP module on ToR switch, packet loss eliminated"),
            ("Firewall rule blocking legitimate API traffic from partner network",
             "Added exception rule for partner IP range, tested connectivity, documented change"),
            ("Load balancer health checks failing for 2 of 5 backend nodes",
             "Restarted application on affected nodes, health checks passing"),
        ],
        "P3": [
            ("Non-critical monitoring server unreachable on management VLAN",
             "VLAN misconfiguration after patch, corrected port assignment"),
            ("SSL certificate expiring in 14 days on internal dashboard",
             "Renewed certificate via internal CA, deployed to load balancer"),
        ],
    },
    "Authentication": {
        "P1": [
            ("SSO provider returning 500 errors, all users locked out",
             "SSO service OOM, restarted with increased heap allocation, sessions restored"),
            ("Active Directory sync broken, new accounts cannot log in",
             "AD connector service crashed, restarted, full sync triggered manually"),
        ],
        "P2": [
            ("MFA not triggering for users in finance group",
             "Policy rule conflict identified in IdP, corrected group assignment, MFA enforced"),
            ("Password reset emails not being delivered",
             "SMTP relay IP blacklisted, switched to secondary relay, emails flowing"),
            ("Service account password expired causing downstream failures",
             "Rotated service account password, updated all dependent configs, restarted services"),
        ],
        "P3": [
            ("User unable to authenticate to legacy ERP system",
             "Legacy system AD integration requires NetBIOS name format, updated user config"),
            ("API token expiry causing nightly batch job failure",
             "Extended token TTL, implemented token refresh logic in batch script"),
        ],
    },
    "Infrastructure": {
        "P1": [
            ("Production web servers CPU at 100% for 20 minutes, site unresponsive",
             "Identified runaway process, killed it, scaled out 2 additional nodes, root cause traced to infinite loop in async job"),
            ("Storage array reporting critical errors, data at risk",
             "Failed disk in RAID group, replaced disk, rebuild initiated, no data loss"),
        ],
        "P2": [
            ("Application server running out of disk space on /var/log",
             "Cleared old logs, implemented log rotation policy, added disk usage alerting"),
            ("Memory leak in Java application causing weekly restarts",
             "Profiled heap dump, identified leak in connection cache, patched and deployed"),
            ("Container orchestration node marked unhealthy, pods evicted",
             "Node kernel panic due to OOM, restarted node, pods rescheduled on healthy nodes"),
        ],
        "P3": [
            ("Non-prod environment deployment pipeline failing",
             "Outdated Terraform provider version, updated to 1.6.x, pipeline green"),
            ("Dev server running slow, developers complaining",
             "Snapshot accumulation filling disk on hypervisor, deleted old snapshots"),
        ],
    },
    "Application": {
        "P1": [
            ("Critical payment processing API returning 503 for all transactions",
             "Downstream payment gateway IP changed without notice, updated firewall rules and DNS"),
            ("Data export feature corrupting files for all enterprise customers",
             "Encoding bug introduced in last deploy, rolled back to v2.4.1, hotfix in progress"),
        ],
        "P2": [
            ("Report generation timing out for large date ranges",
             "Added pagination to report query, implemented async generation with email delivery"),
            ("Mobile app unable to sync after iOS update",
             "iOS 17 changed background fetch behavior, updated app to use new API, submitted to App Store"),
            ("Bulk import feature failing for files over 10MB",
             "Nginx client_max_body_size too low, increased to 50MB, tested with 45MB file"),
        ],
        "P3": [
            ("Dashboard charts not rendering in Safari browser",
             "Chart library version incompatible with Safari 17, updated to v4.2, verified in all browsers"),
            ("Search returning irrelevant results after index rebuild",
             "Incorrect tokenizer settings applied during rebuild, re-indexed with correct config"),
        ],
    },
}

ASSIGNED_GROUPS = [
    "DB-OPS-TEAM", "NETWORK-OPS", "INFRA-TEAM",
    "APP-SUPPORT-L2", "SECURITY-OPS", "PLATFORM-TEAM"
]

OPERATORS = ["rajesh.k", "priya.m", "amit.s", "neha.r", "sudhanshu.n", "swayam.s"]


def random_timestamp(days_back=90):
    delta = random.randint(0, days_back * 24 * 60)
    return datetime.now() - timedelta(minutes=delta)


def calculate_resolution_time(severity):
    ranges = {"P1": (0.5, 4), "P2": (2, 24), "P3": (8, 72)}
    lo, hi = ranges[severity]
    return round(random.uniform(lo, hi), 2)


def generate_ticket(ticket_num):
    category = random.choice(list(TEMPLATES.keys()))
    severity = random.choices(["P1", "P2", "P3"], weights=[15, 35, 50])[0]

    templates = TEMPLATES[category][severity]
    description, resolution = random.choice(templates)

    # Add slight variation so tickets aren't identical
    suffixes = [
        " — reported by monitoring alert.",
        " — escalated by customer.",
        " — noticed during routine check.",
        " — reported via helpdesk portal.",
        " — flagged by automated health check.",
    ]
    description += random.choice(suffixes)

    opened_at = random_timestamp()
    resolution_hrs = calculate_resolution_time(severity)
    resolved_at = opened_at + timedelta(hours=resolution_hrs)

    return {
        "incident_id": f"INC{str(ticket_num).zfill(7)}",
        "short_description": description,
        "category": category,
        "priority": severity,
        "opened_at": opened_at.strftime("%Y-%m-%d %H:%M:%S"),
        "resolved_at": resolved_at.strftime("%Y-%m-%d %H:%M:%S"),
        "resolution_time_hrs": resolution_hrs,
        "close_notes": resolution,
        "assigned_group": random.choice(ASSIGNED_GROUPS),
        "resolved_by": random.choice(OPERATORS),
        "status": "resolved",
    }


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic ITSM tickets")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--output", type=str, default="data/tickets_raw.csv")
    args = parser.parse_args()

    tickets = [generate_ticket(i + 1) for i in range(args.count)]

    fieldnames = tickets[0].keys()
    with open(args.output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(tickets)

    # Print summary
    from collections import Counter
    severities = Counter(t["priority"] for t in tickets)
    categories = Counter(t["category"] for t in tickets)

    print(f"\n✅ Generated {args.count} tickets → {args.output}")
    print(f"\nSeverity breakdown:")
    for sev, count in sorted(severities.items()):
        print(f"  {sev}: {count}")
    print(f"\nCategory breakdown:")
    for cat, count in sorted(categories.items()):
        print(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
