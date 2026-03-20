import csv, random

with open("data/tickets_clean.csv", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

# Pick 5 P1, 8 P2, 7 P3
p1 = [r for r in rows if r["severity"] == "P1"][:5]
p2 = [r for r in rows if r["severity"] == "P2"][:8]
p3 = [r for r in rows if r["severity"] == "P3"][:7]
demo = p1 + p2 + p3
random.shuffle(demo)

# Strip resolution so system must figure it out
for r in demo:
    r["resolution_notes"] = ""
    r["status"] = "open"

if demo:
    with open("data/demo_tickets.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=demo[0].keys())
        writer.writeheader()
        writer.writerows(demo)
    print(f"Created demo_tickets.csv with {len(demo)} tickets (P1:{len(p1)}, P2:{len(p2)}, P3:{len(p3)})")
