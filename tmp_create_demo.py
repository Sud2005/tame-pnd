import csv
import random

def main():
    try:
        with open("data/tickets_clean.csv", "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            tickets = list(reader)
    except FileNotFoundError:
        print("tickets_clean.csv not found.")
        return

    p1 = [t for t in tickets if t["severity"] == "P1"]
    p2 = [t for t in tickets if t["severity"] == "P2"]
    p3 = [t for t in tickets if t["severity"] == "P3"]

    print(f"Found {len(p1)} P1, {len(p2)} P2, {len(p3)} P3 tickets.")

    # Select an even batch
    demo_tickets = ()
    demo_tickets = (
        random.sample(p1, min(len(p1), 6)) +
        random.sample(p2, min(len(p2), 6)) +
        random.sample(p3, min(len(p3), 6))
    )

    random.shuffle(demo_tickets)

    # For demo feed, they should be 'open'
    for t in demo_tickets:
        t["status"] = "open"

    with open("data/demo_tickets.csv", "w", newline="", encoding="utf-8") as f:
        if demo_tickets:
            writer = csv.DictWriter(f, fieldnames=demo_tickets[0].keys())
            writer.writeheader()
            writer.writerows(demo_tickets)
            print(f"Created data/demo_tickets.csv with {len(demo_tickets)} tickets.")

if __name__ == "__main__":
    main()
