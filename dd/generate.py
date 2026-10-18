"""Create reproducible, clearly synthetic dispute cases for evaluation and training.

Cases vary in dispute type, amount, evidence present (tracking, photos, listing text, policy excerpt), delivery
status, and conversation (including seller refund offers and buyer refund demands). About one case in five is
a deliberate fold (very low value, or almost no evidence) so scoring and training see that decision too.
Everything is invented. Same seed, same cases."""
import csv
import os
import random
from datetime import date, timedelta

# name, low price, high price, listing text with a variation note, listing text without one
ITEMS = [
    ("Hand-poured soy candle", 14, 28, "Hand-poured soy candle in a reusable tin. Scent strength and color can vary slightly between batches.", "Hand-poured soy candle in a reusable tin. Burns for about 40 hours."),
    ("Ceramic planter", 28, 55, "Hand-glazed ceramic planter. Each piece is unique, so glaze color and pattern vary slightly.", "Glazed ceramic planter, 6 inches wide, with a drainage hole."),
    ("Linen kitchen towel set", 22, 40, "Set of two stonewashed linen towels. Color may look different on different screens.", "Set of two stonewashed linen towels, 18 by 28 inches."),
    ("Hand-dyed wool scarf", 60, 95, "Hand-dyed wool scarf. Each piece is dyed by hand, so color may vary slightly from the photos.", "Hand-dyed wool scarf, 70 inches long, in the color shown."),
    ("Leather journal", 32, 58, "Hand-stitched leather journal. Natural leather has marks and color variation.", "Hand-stitched leather journal with 200 pages."),
    ("Wooden desk organizer", 38, 70, "Solid walnut desk organizer. Wood grain differs from piece to piece.", "Solid walnut desk organizer with three compartments."),
    ("Embroidered tote bag", 24, 44, "Cotton tote bag with hand embroidery. Thread color may differ slightly from the photos.", "Cotton tote bag, 15 by 16 inches, with an inside pocket."),
    ("Art print set", 18, 36, "Set of three art prints. Colors on paper can differ from your screen.", "Set of three 8 by 10 inch art prints on matte paper."),
]
NAMES = ["Asha P.", "Ben L.", "Carla M.", "Dev R.", "Elena S.", "Farah K.", "Jon T.", "Mina V.", "Noah W.", "Priya N.", "Sam O.", "Tara H."]
KINDS = [("not_received", "item not received"), ("not_as_described", "item not as described"), ("damaged", "item arrived damaged")]
REASONS = {
    "not_received": ["The package never arrived.", "I have not received my order.", "Tracking says delivered but nothing is here.", "My order has not arrived and it is well past the delivery date."],
    "not_as_described": ["The color is different from the listing photos.", "The item is smaller than I expected.", "The material does not look like what was described.", "It does not match the photos in the listing."],
    "damaged": ["The item arrived damaged.", "The item arrived broken.", "There is a crack on one side of the item.", "The box was crushed and the item inside is damaged."],
}
BUYER_OPENERS = {
    "not_received": ["Hi, my order has not arrived yet. Can you check on it?", "I still do not have my package. Can you look into it?", "Tracking says delivered but I cannot find the package."],
    "not_as_described": ["The item does not match the listing. Can you help?", "This is not what I expected from the photos.", "I think the item is different from how it was described."],
    "damaged": ["My order arrived damaged. Can you help?", "The box was crushed and the item inside is broken.", "There is a crack on one side of the item."],
}
POLICY = "SYNTHETIC PLACEHOLDER: Sellers should keep proof of shipment and delivery in case a buyer opens a dispute."
PHOTO_NAMES = {"not_received": ["packed_parcel.jpg", "drop_off_receipt.jpg", "label_and_box.jpg"], "other": ["item_front.jpg", "box_and_item.jpg", "item_before_packing.jpg"]}
LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _tracking(rng, carrier):
    if carrier == "USPS":
        return f"94001{rng.randrange(10**17, 10**18)}"
    if carrier == "UPS":
        return "1Z" + "".join(rng.choice(LETTERS) for _ in range(6)) + str(rng.randrange(10**9, 10**10))
    return str(rng.randrange(10**11, 10**12))


def _say(d):
    return f"{d:%B} {d.day}"


def _case(rng, number):
    kind, type_raw = rng.choice(KINDS)
    item, low, high, listing_var, listing_plain = rng.choice(ITEMS)
    roll = rng.random()
    scenario = "low_value" if roll < 0.12 else "thin" if roll < 0.20 else "normal"
    amount = float(rng.randrange(3, 10)) + rng.choice([0.0, 0.5]) if scenario == "low_value" else float(rng.randrange(low, high + 1))
    carrier = rng.choice(["USPS", "UPS", "FedEx"])
    order_date = date(2026, 1, 1) + timedelta(days=rng.randrange(260))
    ship_date = order_date + timedelta(days=rng.randrange(1, 4))
    delivery_date = ship_date + timedelta(days=rng.randrange(2, 7))
    has_tracking = scenario != "thin" and rng.random() < 0.92
    delivered = has_tracking and (kind != "not_received" or rng.random() < 0.5)
    status = "delivered" if delivered else ("in transit" if has_tracking else "")
    opened = (delivery_date if delivered else ship_date + timedelta(days=rng.randrange(8, 15))) + timedelta(days=rng.randrange(2, 9))
    if scenario == "thin":
        has_msgs, has_photo, has_listing, has_policy = False, False, False, False
    else:
        has_msgs = True
        has_photo = rng.random() < 0.5
        has_listing = kind != "not_received" and rng.random() < 0.75
        has_policy = rng.random() < 0.2
    variation_note = rng.random() < 0.6
    return {"name": f"case_{number:03d}_{kind}", "kind": kind, "type_raw": type_raw, "reason": rng.choice(REASONS[kind]),
            "item": item, "amount": amount, "buyer": rng.choice(NAMES), "carrier": carrier,
            "order_id": str(rng.randrange(1000000, 9999999)), "tracking": _tracking(rng, carrier) if has_tracking else "",
            "order_date": order_date, "ship_date": ship_date, "delivery_date": delivery_date if delivered else None,
            "opened": opened, "status": status, "has_msgs": has_msgs, "has_photo": has_photo, "has_listing": has_listing,
            "has_policy": has_policy, "listing": listing_var if variation_note else listing_plain,
            "photo": rng.choice(PHOTO_NAMES["not_received" if kind == "not_received" else "other"]),
            "refund_offer": rng.random() < 0.4, "buyer_demand": rng.random() < 0.5, "opener": rng.choice(BUYER_OPENERS[kind]),
            "hour": rng.randrange(8, 20), "minute": rng.randrange(0, 60)}


def _messages(c):
    def stamp(d, add=0):
        return f"{d + timedelta(days=add):%Y-%m-%d} {c['hour']:02d}:{c['minute']:02d}"
    out = [f"{stamp(c['opened'], -1)} | buyer | {c['opener']}"]
    if c["kind"] == "not_received":
        if c["delivery_date"]:
            reply = f"Tracking shows it was delivered on {_say(c['delivery_date'])}. Could you check with neighbors or your building office?"
        elif c["tracking"]:
            reply = "The tracking shows it is still in transit. It should arrive soon, and I will keep watching it."
        else:
            reply = "I am checking my shipping records and will get back to you."
    elif c["kind"] == "not_as_described":
        reply = "Each piece is made by hand, so small differences can happen."
        if c["refund_offer"]:
            reply += " I am happy to offer a partial refund if you would like."
    else:
        reply = "I am sorry to hear that. I packed it in a double-walled box with bubble wrap."
        if c["refund_offer"]:
            reply += " I can offer a replacement or a partial refund."
    out.append(f"{stamp(c['opened'])} | seller | {reply}")
    if c["buyer_demand"]:
        out.append(f"{stamp(c['opened'], 1)} | buyer | No, I want a full refund.")
    return out


def _write_case(root, c):
    path = os.path.join(root, c["name"])
    os.makedirs(path, exist_ok=False)
    _write(os.path.join(path, "notice.txt"), "\n".join([
        "# SYNTHETIC EXAMPLE. Not a real seller or buyer.", "platform: etsy", f"type: {c['type_raw']}", f"reason: {c['reason']}",
        f"amount: ${c['amount']:.2f}", f"opened: {c['opened'].isoformat()}"]) + "\n")
    with open(os.path.join(path, "order.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "buyer_name", "item", "amount", "order_date", "ship_date", "carrier", "tracking_number", "delivery_status", "delivery_date"])
        w.writerow([c["order_id"], c["buyer"], c["item"], f"{c['amount']:.2f}", c["order_date"].isoformat(),
                    c["ship_date"].isoformat() if c["tracking"] else "", c["carrier"] if c["tracking"] else "", c["tracking"],
                    c["status"], c["delivery_date"].isoformat() if c["delivery_date"] else ""])
    _write(os.path.join(path, "messages.txt"), "\n".join(_messages(c) if c["has_msgs"] else []) + ("\n" if c["has_msgs"] else ""))
    if c["has_listing"]:
        _write(os.path.join(path, "listing.txt"), c["listing"] + "\n")
    if c["has_policy"]:
        _write(os.path.join(path, "policy.txt"), POLICY + "\n")
    if c["has_photo"]:
        os.makedirs(os.path.join(path, "photos"))
        _write(os.path.join(path, "photos", c["photo"]), "synthetic placeholder; the file name is the evidence")


def generate_cases(out_dir, count=120, seed=20261013):
    """Generate *count* independent case folders and return their names."""
    if count < 1:
        raise ValueError("count must be at least 1")
    os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(seed)
    names = []
    for number in range(1, count + 1):
        c = _case(rng, number)
        _write_case(out_dir, c)
        names.append(c["name"])
    return names
