"""Generate synthetic purchase history for the recommender.

Each simulated shopper takes on one to three "projects" (painting a room,
fixing a leak, ...) and buys most of the items that project needs, plus the
occasional unrelated item. That gives the co-purchase patterns that
item-based collaborative filtering learns from.

Usage:  python scripts/generate_purchases.py
"""
import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

PROJECTS = {
    "furniture assembly": ["hex-key-set", "rubber-mallet", "wood-glue", "felt-pads", "phillips-screwdriver", "cordless-drill"],
    "hanging pictures": ["claw-hammer", "picture-hanging-kit", "spirit-level", "stud-finder", "drywall-anchors", "tape-measure", "cordless-drill", "drill-bit-set"],
    "painting a room": ["painters-tape", "paint-roller", "paint-tray", "angled-brush", "drop-cloth", "spackle", "putty-knife", "sandpaper", "sanding-sponge"],
    "plumbing repair": ["plunger", "thread-seal-tape", "pipe-wrench", "adjustable-wrench", "drain-snake", "faucet-aerator", "toilet-flapper"],
    "re-caulking a bath": ["caulk-gun", "silicone-caulk", "utility-knife", "painters-tape", "rubber-gloves", "scrub-brush"],
    "electrical work": ["wire-stripper", "voltage-tester", "wire-nuts", "electrical-tape", "needle-nose-pliers", "flathead-screwdriver", "led-bulbs"],
    "power outage kit": ["flashlight", "headlamp", "batteries-aa", "extension-cord", "power-strip"],
    "cooking basics": ["silicone-spatula", "whisk", "colander", "tongs", "cast-iron-skillet", "measuring-cups"],
    "food prep": ["vegetable-peeler", "garlic-press", "zester", "bench-scraper", "can-opener"],
    "deep cleaning": ["microfiber-cloths", "squeegee", "spray-bottle", "scrub-brush", "melamine-sponge", "rubber-gloves", "dustpan-brush", "lint-roller"],
    "planting": ["garden-trowel", "pruning-shears", "garden-gloves", "kneeling-pad", "watering-can", "garden-twine"],
    "watering": ["garden-hose", "hose-nozzle", "watering-can", "thread-seal-tape"],
    "desk setup": ["binder-clips", "sticky-notes", "staple-remover", "desk-organizer", "label-maker", "cable-clips", "power-strip"],
    "moving house": ["packing-tape", "bubble-wrap", "utility-knife", "duct-tape", "zip-ties", "bungee-cords", "label-maker", "felt-pads"],
    "garage organizing": ["s-hooks", "zip-ties", "bungee-cords", "wood-screws", "drywall-anchors", "cordless-drill", "lubricant-spray", "hacksaw", "c-clamp"],
    "baby proofing": ["outlet-covers", "door-stop", "cable-clips", "felt-pads", "super-glue"],
}

N_SHOPPERS = 400
BUY_PROBABILITY = 0.65  # chance of buying each item a project needs


def main() -> None:
    rng = random.Random(42)
    product_ids = [p["id"] for p in json.loads((DATA / "products.json").read_text())]
    unknown = {i for items in PROJECTS.values() for i in items} - set(product_ids)
    if unknown:
        raise SystemExit(f"Projects reference unknown products: {sorted(unknown)}")

    project_names = list(PROJECTS)
    today = date(2026, 11, 15)
    rows = []
    for n in range(1, N_SHOPPERS + 1):
        shopper = f"u{n:03d}"
        basket = set()
        for project in rng.sample(project_names, rng.choice([1, 1, 2, 2, 2, 3])):
            basket.update(i for i in PROJECTS[project] if rng.random() < BUY_PROBABILITY)
        basket.update(rng.sample(product_ids, rng.choice([0, 0, 1, 1, 2])))  # impulse buys
        for item in sorted(basket):
            when = today - timedelta(days=rng.randint(0, 364))
            rows.append((shopper, item, rng.choice([1, 1, 1, 2]), when.isoformat()))

    rows.sort(key=lambda r: (r[3], r[0], r[1]))
    with (DATA / "purchases.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["user_id", "product_id", "quantity", "purchased_at"])
        writer.writerows(rows)
    print(f"Wrote {len(rows)} purchases by {N_SHOPPERS} shoppers to {DATA / 'purchases.csv'}")


if __name__ == "__main__":
    main()
