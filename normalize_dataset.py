"""
Phase 2 — ITSM Dataset Normalizer (Updated)
=============================================
Built specifically for the actual ITSM_data.csv columns:
  CI_Name, CI_Cat, CI_Subcat, WBS, Incident_ID, Status,
  Impact, Urgency, Priority, Category, Alert_Status,
  No_of_Reassignments, Open_Time, Handle_Time_hrs,
  Closure_Code, No_of_Related_Interactions, etc.

Usage:
    python normalize_dataset.py --input ITSM_data.csv --output data/tickets_clean.csv
"""

import argparse
import csv
import sys
from datetime import datetime
from collections import Counter

# ── Priority map — handles numeric 1-5 from ITSM_data.csv ──────────────────

PRIORITY_MAP = {
    "1":"P1","2":"P2","3":"P3","4":"P3","5":"P3",
    "p1":"P1","p2":"P2","p3":"P3",
    "critical":"P1","high":"P2","medium":"P3","low":"P3",
    "1.0":"P1","2.0":"P2","3.0":"P3","4.0":"P3",
}

# ── Exact column mapping for ITSM_data.csv ──────────────────────────────────
# Key = our schema field, Values = possible column names in dataset

COLUMN_ALIASES = {
    "incident_id":      ["Incident_ID","incident_id","number","id","ticket_id"],
    "description":      ["CI_Name","short_description","description","summary","title"],
    "ci_cat":           ["CI_Cat","ci_cat","category_type"],
    "ci_subcat":        ["CI_Subcat","ci_subcat","subcategory"],
    "wbs":              ["WBS","wbs","work_breakdown"],
    "priority":         ["Priority","priority","severity","Impact","impact","Urgency","urgency"],
    "urgency":          ["Urgency","urgency"],
    "impact":           ["Impact","impact"],
    "category":         ["Category","category","incident_type"],
    "alert_status":     ["Alert_Status","alert_status"],
    "no_reassignments": ["No_of_Reassignments","reassignments"],
    "opened_at":        ["Open_Time","opened_at","created_at","open_time","date_opened"],
    "resolved_at":      ["Resolved_Time","resolved_at","close_time","Close_Time"],
    "handle_time_hrs":  ["Handle_Time_hrs","handle_time","resolution_time_hrs"],
    "closure_code":     ["Closure_Code","closure_code","close_notes","resolution_notes"],
    "status":           ["Status","status"],
    "related_incidents":["No_of_Related_Incidents","related_incidents"],
    "related_changes":  ["No_of_Related_Changes","related_changes"],
    "kb_number":        ["KB_number","kb_number","knowledge_article"],
}


def detect_column(all_cols, field):
    lowmap = {c.lower().strip(): c for c in all_cols}
    for alias in COLUMN_ALIASES.get(field, []):
        if alias.lower() in lowmap:
            return lowmap[alias.lower()]
    return None


def parse_datetime(val):
    fmts = [
        "%d/%m/%Y %H:%M","%m/%d/%Y %H:%M",
        "%Y-%m-%d %H:%M:%S","%Y-%m-%dT%H:%M:%S",
        "%d-%m-%Y %H:%M","%Y-%m-%d",
        "%d/%m/%Y","%m/%d/%Y",
    ]
    val = str(val).strip()
    for fmt in fmts:
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


def calculate_mttr(opened, resolved):
    o = parse_datetime(opened)
    r = parse_datetime(resolved)
    if o and r and r > o:
        return round((r - o).total_seconds() / 3600, 2)
    return None


def infer_description(row, col_map, all_cols):
    ci_name   = _get(row, col_map, "description", "")
    ci_cat    = _get(row, col_map, "ci_cat", "")
    ci_subcat = _get(row, col_map, "ci_subcat", "")
    urgency   = _get(row, col_map, "urgency", "")
    impact    = _get(row, col_map, "impact", "")
    alert     = _get(row, col_map, "alert_status", "")
    no_reassign = _get(row, col_map, "no_reassignments", "0")

    # Build a meaningful description instead of just "Incident with X on Y"
    parts = []

    subcat_map = {
        "Desktop Application": "Desktop application issue",
        "Web Based Application": "Web application failure",
        "Server Based Application": "Server application error",
        "Network Infrastructure": "Network connectivity issue",
        "SAN Storage": "Storage system failure",
        "Laptop": "Laptop hardware issue",
    }
    base = subcat_map.get(ci_subcat, f"{ci_subcat or ci_cat or 'System'} incident")
    parts.append(base)

    if ci_name and ci_name not in ("nan", "none", ""):
        parts.append(f"on device {ci_name}")

    if urgency in ("1", "2", "1.0", "2.0"):
        parts.append("with high urgency")
    if impact in ("1", "2", "1.0", "2.0"):
        parts.append("affecting multiple users")
    if alert.lower() == "true":
        parts.append("— active monitoring alert triggered")
    if no_reassign and int(float(no_reassign)) > 2:
        parts.append(f"— reassigned {no_reassign} times indicating complexity")

    return ". ".join(parts)


def _get(row, col_map, field, default=""):
    col = col_map.get(field)
    if col and col in row:
        v = str(row[col]).strip()
        return v if v.lower() not in ("nan","none","") else default
    return default


def normalize_row(row, col_map, row_num):
    raw_priority = _get(row, col_map, "priority", "3")
    severity     = PRIORITY_MAP.get(raw_priority.lower(), "P3")

    raw_id  = _get(row, col_map, "incident_id", "")
    tid     = raw_id if raw_id else f"INC{str(row_num).zfill(7)}"

    description = infer_description(row, col_map, row.keys())

    opened_str   = _get(row, col_map, "opened_at", "")
    resolved_str = _get(row, col_map, "resolved_at", "")

    # Use Handle_Time_hrs directly from dataset if available
    handle_time = _get(row, col_map, "handle_time_hrs", "")
    try:
        mttr = float(handle_time) if handle_time else calculate_mttr(opened_str, resolved_str)
    except ValueError:
        mttr = calculate_mttr(opened_str, resolved_str)

    closure_code = _get(row, col_map, "closure_code", "")
    raw_status   = _get(row, col_map, "status", "").lower()

    # Map ITSM status values
    if raw_status in ("closed","resolved","completed"):
        status = "resolved"
    elif raw_status in ("open","new","in progress","active"):
        status = "open"
    else:
        status = "resolved" if closure_code else "open"

    # Derive category from CI_Cat if possible
    ci_cat = _get(row, col_map, "ci_cat", "")
    ci_cat_to_category = {
        "storage": "Database", "database": "Database",
        "network": "Network", "networkcomponents": "Network",
        "hardware": "Infrastructure", "displaydevice": "Infrastructure",
        "application": "Application", "subapplication": "Application",
        "applicationcomponent": "Application",
    }
    category = ci_cat_to_category.get(ci_cat.lower(), "General")

    return {
        "id":                   tid,
        "description":          description,
        "severity":             severity,
        "category":             category,
        "ci_cat":               ci_cat,
        "ci_subcat":            _get(row, col_map, "ci_subcat", ""),
        "urgency":              _get(row, col_map, "urgency", ""),
        "impact":               _get(row, col_map, "impact", ""),
        "alert_status":         _get(row, col_map, "alert_status", "False"),
        "no_reassignments":     _get(row, col_map, "no_reassignments", "0"),
        "opened_at":            opened_str or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "resolved_at":          resolved_str,
        "resolution_time_hrs":  mttr or "",
        "resolution_notes":     closure_code,
        "kb_number":            _get(row, col_map, "kb_number", ""),
        "related_incidents":    _get(row, col_map, "related_incidents", "0"),
        "status":               status,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",  required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit",  type=int, default=None)
    args = parser.parse_args()

    print(f"\n📂 Reading: {args.input}")
    with open(args.input, newline="", encoding="utf-8-sig") as f:
        reader   = csv.DictReader(f)
        raw_rows = list(reader)
        raw_cols = reader.fieldnames or []

    print(f"   Rows: {len(raw_rows):,}  |  Columns: {len(raw_cols)}")

    print(f"\n🔍 Column mapping:")
    col_map = {}
    for field in COLUMN_ALIASES:
        detected = detect_column(raw_cols, field)
        col_map[field] = detected
        icon = "✅" if detected else "⚠️ "
        print(f"   {icon} {field:<22} → {detected or 'NOT FOUND (default used)'}")

    if not col_map.get("description") and not col_map.get("ci_cat"):
        print("\n❌ Cannot find key columns. Check your CSV.")
        sys.exit(1)

    to_process = raw_rows[:args.limit] if args.limit else raw_rows
    cleaned, skipped = [], 0

    for i, row in enumerate(to_process):
        try:
            cleaned.append(normalize_row(row, col_map, i+1))
        except Exception as e:
            skipped += 1
            if skipped <= 3:
                print(f"   ⚠️  Row {i+1}: {e}")

    if not cleaned:
        print("❌ No rows normalized.")
        sys.exit(1)

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(cleaned[0].keys()))
        writer.writeheader()
        writer.writerows(cleaned)

    sev_counts = Counter(r["severity"] for r in cleaned)
    cat_counts  = Counter(r["category"] for r in cleaned)
    resolved    = sum(1 for r in cleaned if r["status"] == "resolved")
    alerted     = sum(1 for r in cleaned if r["alert_status"].lower() == "true")

    print(f"\n✅ Normalized {len(cleaned):,} rows → {args.output}")
    if skipped: print(f"   ⚠️  Skipped: {skipped}")
    print(f"\n   Severity: {dict(sev_counts)}")
    print(f"   Resolved: {resolved:,} / {len(cleaned):,}")
    print(f"   Active alerts: {alerted:,}")
    print(f"\n   Top categories: {dict(cat_counts.most_common(4))}")
    print(f"\n   Sample row:")
    for k, v in list(cleaned[0].items())[:8]:
        print(f"     {k}: {str(v)[:55]}")


if __name__ == "__main__":
    main()
