from .models import Evidence, Gap


def classify(dispute):
    blob = f"{dispute.notice.type_raw} {dispute.notice.reason}".lower()
    if any(k in blob for k in ("not received", "never arrived", "didn't arrive", "did not arrive", "not arrive", "hasn't arrived", "has not arrived", "never came", "never got", "not delivered", "lost in transit", "missing package", "never showed", "never received", "haven't received", "have not received", "didn't receive", "did not receive", "not arrived", "wasn't delivered", "was not delivered", "hasn't been delivered", "nothing arrived")):
        return "not_received"
    if any(k in blob for k in ("not as described", "different from", "differs", "wrong item", "damaged", "color", "colour", "size")):
        return "not_as_described"
    return "unknown"


def _clean(text):
    return str(text).replace('"', "'").strip()


def build_evidence(dispute, dispute_type):
    n, o = dispute.notice, dispute.order
    items = []

    def add(kind, etext, **fields):
        items.append(Evidence(id=f"E{len(items) + 1}", kind=kind, text=etext, fields=fields))

    add("dispute_notice", f'Case opened {n.opened}: "{_clean(n.reason)}". Amount in dispute: ${n.amount:.2f}. Type: {n.type_raw}',
        opened=n.opened, reason=_clean(n.reason))
    if o.order_id:
        add("order_record", f"Order {o.order_id}: {o.item}, ${o.amount:.2f}, placed {o.order_date}, buyer {o.buyer_name}",
            order_id=o.order_id, item=o.item, amount=o.amount, order_date=o.order_date)
    if o.tracking_number:
        t = f"Shipped {o.ship_date} via {o.carrier}, tracking {o.tracking_number}"
        if o.delivery_status and o.delivery_status != "delivered":
            t += f". Carrier status: {o.delivery_status}"
        add("shipping_tracking", t, ship_date=o.ship_date, carrier=o.carrier, tracking=o.tracking_number)
        if o.delivery_status == "delivered" and o.delivery_date:
            add("delivery_confirmation", f"Carrier shows delivered on {o.delivery_date} (tracking {o.tracking_number})",
                delivery_date=o.delivery_date)
    for name in dispute.photos:
        kind = "item_photos" if dispute_type == "not_as_described" else "shipping_proof_photo"
        add(kind, f"Photo on file: {name}", filename=name)
    for m in dispute.messages:
        add("buyer_communication", f"{m.ts} {m.sender}: {_clean(m.text)}", ts=m.ts, sender=m.sender, text=_clean(m.text))
    if dispute.listing_text:
        add("listing_description", f"Listing text: {_clean(dispute.listing_text)}", text=_clean(dispute.listing_text))
    if dispute.policy_text:
        add("policy_reference", f"Policy excerpt: {_clean(dispute.policy_text)}", text=_clean(dispute.policy_text))
    return items


def find_gaps(evidence, checklist):
    have = {e.kind for e in evidence}
    return [Gap(slot=s, label=label, how=how) for s, label, how in checklist if s not in have]
