"""
Phase 1 — Dataset Normalizer
==============================
Converts raw CSV (Kaggle OR synthetic) into your system's clean ticket schema.
Handles messy column names, missing values, and priority format differences.

Usage:
    python scripts/normalize_dataset.py --input data/tickets_raw.csv --output data/tickets_clean.csv
    python scripts/normalize_dataset.py --input data/kaggle_itsm.csv --output data/tickets_clean.csv
"""

import argparse
import json
import csv
import sys
from datetime import datetime


# ─── Priority / Severity mapping (covers all known Kaggle formats) ──────────

PRIORITY_MAP = {
    # Numeric
    "1": "P1", "2": "P2", "3": "P3", "4": "P3",
    # Text (ServiceNow Kaggle dataset)
    "critical": "P1", "high": "P2", "medium": "P3", "low": "P3",
    # With "priority" prefix
    "priority 1": "P1", "priority 2": "P2", "priority 3": "P3",
    # Other formats
    "p1": "P1", "p2": "P2", "p3": "P3",
    "urgent": "P1", "normal": "P3",
}

# ─── Column name aliases (maps Kaggle column names → your schema) ────────────

COLUMN_ALIASES = {
    "description":        ["short_description", "description", "incident_description",
                           "summary", "title", "issue_description", "ci_name"],
    "category":           ["category", "incident_type", "type", "service_category",
                           "assignment_group_category"],
    "priority":           ["priority", "severity", "impact", "urgency", "sla_priority"],
    "opened_at":          ["opened_at", "created_at", "open_time", "incident_date",
                           "date_opened", "created_date", "timestamp"],
    "resolved_at":        ["resolved_at", "closed_at", "resolution_date", "close_time",
                           "date_closed", "resolved_date"],
    "resolution_notes":   ["close_notes", "resolution_notes", "resolution",
                           "resolution_description", "fix_description", "close_code", "closure_code"],
    "assigned_group":     ["assigned_group", "assignment_group", "team", "support_group",
                           "resolver_group"],
    "resolved_by":        ["resolved_by", "closed_by", "resolver", "technician",
                           "assigned_to"],
    "incident_id":        ["incident_id", "number", "id", "ticket_id", "sys_id",
                           "case_number"],
}


def detect_column(df_columns, field_name):
    """Find which actual column name corresponds to a schema field."""
    df_cols_lower = {c.lower().strip(): c for c in df_columns}
    for alias in COLUMN_ALIASES.get(field_name, []):
        if alias.lower() in df_cols_lower:
            return df_cols_lower[alias.lower()]
    return None


def parse_datetime(value):
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%Y-%m-%d",
        "%d-%m-%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            continue
    return None


def calculate_mttr(opened_str, resolved_str):
    opened = parse_datetime(opened_str)
    resolved = parse_datetime(resolved_str)
    if opened and resolved and resolved > opened:
        delta = resolved - opened
        return round(float(delta.total_seconds() / 3600.0), 2)  # type: ignore
    return None


def normalize_row(row, col_map, row_num):
    """Convert one raw CSV row into clean ticket schema."""

    def get(field, default=""):
        col = col_map.get(field)
        if col and col in row:
            return str(row[col]).strip()
        return default

    raw_priority = get("priority", "P3").lower().strip()
    severity = PRIORITY_MAP.get(raw_priority, "P3")

    raw_id = get("incident_id", "").strip()
    ticket_id = raw_id if raw_id else f"INC{str(row_num).zfill(7)}"

    description = get("description", "No description provided")
    if not description or description.lower() in ("nan", "none", ""):
        description = "No description provided"

    resolution = get("resolution_notes", "")
    if resolution.lower() in ("nan", "none", ""):
        resolution = ""

    category = get("category", "General")
    if category.lower() in ("nan", "none", ""):
        category = "General"

    opened_str = get("opened_at")
    resolved_str = get("resolved_at")
    mttr = calculate_mttr(opened_str, resolved_str)

    # Status: resolved only if there are resolution notes
    status = "resolved" if resolution else "open"

    return {
        "id":                 ticket_id,
        "description":        description,
        "severity":           severity,
        "category":           category,
        "opened_at":          opened_str or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "resolved_at":        resolved_str or "",
        "resolution_time_hrs": mttr or "",
        "resolution_notes":   resolution,
        "assigned_group":     get("assigned_group", "SUPPORT-TEAM"),
        "resolved_by":        get("resolved_by", "unknown"),
        "status":             status,
    }


def main():
    parser = argparse.ArgumentParser(description="Normalize ITSM dataset to clean schema")
    parser.add_argument("--input",  required=True,  help="Path to raw CSV file")
    parser.add_argument("--output", required=True,  help="Path for cleaned CSV output")
    parser.add_argument("--limit",  type=int, default=None, help="Max rows to process")
    args = parser.parse_args()

    print(f"\n📂 Reading: {args.input}")

    with open(args.input, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        raw_rows = list(reader)
        raw_columns = reader.fieldnames or []

    print(f"   Found {len(raw_rows)} rows, {len(raw_columns)} columns")
    print(f"   Columns: {raw_columns[:8]}{'...' if len(raw_columns) > 8 else ''}")  # type: ignore

    # Auto-detect column mapping
    col_map = {}
    print(f"\n🔍 Detecting column mapping:")
    for field in COLUMN_ALIASES:
        detected = detect_column(raw_columns, field)
        col_map[field] = detected
        status = f"✅ → '{detected}'" if detected else "⚠️  NOT FOUND (will use default)"
        print(f"   {field:<20} {status}")

    if not col_map.get("description"):
        print("\n❌ Could not find a description column. Check your CSV column names.")
        sys.exit(1)

    # Normalize
    rows_to_process = raw_rows[:args.limit] if args.limit else raw_rows  # type: ignore
    cleaned = []
    skipped: int = 0

    for i, row in enumerate(rows_to_process):
        try:
            cleaned_row = normalize_row(row, col_map, i + 1)
            cleaned.append(cleaned_row)
        except Exception as e:
            skipped += 1  # type: ignore
            if skipped <= 3:
                print(f"   ⚠️  Skipped row {i+1}: {e}")

    # Write output
    if not cleaned:
        print("❌ No rows were successfully normalized.")
        sys.exit(1)

    fieldnames = list(cleaned[0].keys())
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(cleaned)

    # Stats
    from collections import Counter
    sev_counts = Counter(r["severity"] for r in cleaned)
    cat_counts  = Counter(r["category"] for r in cleaned)
    resolved_count = sum(1 for r in cleaned if r["status"] == "resolved")

    print(f"\n✅ Normalized {len(cleaned)} rows → {args.output}")
    if skipped:
        print(f"   ⚠️  Skipped {skipped} malformed rows")
    print(f"\n   Severity:  {dict(sev_counts)}")
    print(f"   Resolved:  {resolved_count} / {len(cleaned)}")
    print(f"\n   Top categories:")
    for cat, count in cat_counts.most_common(5):
        print(f"     {cat}: {count}")
    print(f"\n   Sample row:")
    sample = cleaned[0]
    for k, v in sample.items():
        print(f"     {k}: {str(v)[:60]}")  # type: ignore


if __name__ == "__main__":
    main()
