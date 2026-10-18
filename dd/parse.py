import csv
import io
import os

from .models import Dispute, Message, Notice, Order


def read_text(path):
    """Read a text file that may have been written by Windows PowerShell (UTF-16 or ANSI) or an editor (UTF-8 with BOM)."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _kv(path):
    out = {}
    if not os.path.exists(path):
        return out
    for line in read_text(path).splitlines():
        line = line.strip()
        if line and not line.startswith("#") and ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _money(v):
    try:
        return float(str(v).replace("$", "").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _read(path):
    if not os.path.exists(path):
        return ""
    return "\n".join(l for l in read_text(path).splitlines() if not l.startswith("#")).strip()


def dispute_from_dict(d):
    n = d.get("notice", {})
    o = d.get("order", {})
    notice = Notice(type_raw=n.get("type", n.get("type_raw", "")), reason=n.get("reason", ""),
                    amount=_money(n.get("amount", 0)), opened=n.get("opened", ""), deadline=n.get("deadline", ""))
    order = Order(order_id=str(o.get("order_id", "")), buyer_name=o.get("buyer_name", ""), item=o.get("item", ""),
                  amount=_money(o.get("amount", 0)), order_date=o.get("order_date", ""), ship_date=o.get("ship_date", ""),
                  carrier=o.get("carrier", ""), tracking_number=o.get("tracking_number", ""),
                  delivery_status=o.get("delivery_status", ""), delivery_date=o.get("delivery_date", ""))
    msgs = [Message(m["ts"], m["sender"], m["text"]) for m in d.get("messages", [])]
    return Dispute(platform=d.get("platform", "etsy"), notice=notice, order=order, messages=msgs,
                   photos=list(d.get("photos", [])), policy_text=d.get("policy_text", ""),
                   listing_text=d.get("listing_text", ""))


def load_case(case_dir):
    """Read a case folder: notice.txt, order.csv (first row used), messages.txt, optional policy.txt, listing.txt, photos/."""
    if not os.path.isdir(case_dir):
        raise ValueError(f"case folder not found: {case_dir}")
    if not os.path.exists(os.path.join(case_dir, "notice.txt")):
        raise ValueError(f"{case_dir} has no notice.txt. A case folder needs notice.txt, order.csv and messages.txt (see README).")
    notice = _kv(os.path.join(case_dir, "notice.txt"))
    order = {}
    p = os.path.join(case_dir, "order.csv")
    if os.path.exists(p):
        rows = list(csv.DictReader(io.StringIO(read_text(p))))
        order = {(k or "").strip(): (v or "").strip() for k, v in rows[0].items()} if rows else {}
    messages = []
    mp = os.path.join(case_dir, "messages.txt")
    if os.path.exists(mp):
        for line in read_text(mp).splitlines():
            parts = [x.strip() for x in line.split("|", 2)]
            if len(parts) == 3 and not line.startswith("#"):
                messages.append({"ts": parts[0], "sender": parts[1].lower(), "text": parts[2]})
    photo_dir = os.path.join(case_dir, "photos")
    photos = sorted(os.listdir(photo_dir)) if os.path.isdir(photo_dir) else []
    return dispute_from_dict({
        "platform": notice.get("platform", "etsy"),
        "notice": notice, "order": order, "messages": messages, "photos": photos,
        "policy_text": _read(os.path.join(case_dir, "policy.txt")),
        "listing_text": _read(os.path.join(case_dir, "listing.txt")),
    })
