"""Etsy adapter.

IMPORTANT: the response window and the checklist below are PLACEHOLDERS that encode a
reasonable structure, not verified Etsy policy. Verify against Etsy's current seller
policy before anyone relies on them. The pipeline never invents policy text: the policy
slot is only filled when the seller supplies a policy excerpt (policy.txt).
"""
from datetime import date, timedelta

Slot = tuple  # (slot_id, label, how_to_get)

_COMMON = [
    ("order_record", "Order record", "Export the order from your shop manager."),
]

CHECKLISTS = {
    "not_received": _COMMON + [
        ("shipping_tracking", "Tracking and ship date", "Add the tracking number and ship date from your shipping label."),
        ("delivery_confirmation", "Delivery confirmation", "Check the carrier site for a delivery scan, or request proof of delivery from the carrier."),
        ("shipping_proof_photo", "Packing or shipping photo", "Add a photo of the packed parcel or the drop-off receipt, if you have one."),
        ("buyer_communication", "Buyer messages", "Paste the message thread with the buyer."),
        ("policy_reference", "Policy excerpt", "Paste the relevant clause from the platform's current seller policy."),
    ],
    "not_as_described": _COMMON + [
        ("listing_description", "Listing text", "Paste the item description as it was listed at purchase time."),
        ("item_photos", "Photos of the item sent", "Add photos of the item as it was packed."),
        ("buyer_communication", "Buyer messages", "Paste the message thread with the buyer."),
        ("shipping_tracking", "Tracking and ship date", "Add the tracking number and ship date."),
        ("delivery_confirmation", "Delivery confirmation", "Check the carrier site for a delivery scan."),
        ("policy_reference", "Policy excerpt", "Paste the relevant clause from the platform's current seller policy."),
    ],
    "unknown": _COMMON + [
        ("buyer_communication", "Buyer messages", "Paste the message thread with the buyer."),
    ],
}

# How much each slot is worth when estimating win likelihood. Heuristic and uncalibrated.
WEIGHTS = {
    "not_received": {"order_record": 0.10, "shipping_tracking": 0.20, "delivery_confirmation": 0.35,
                     "shipping_proof_photo": 0.15, "buyer_communication": 0.10, "policy_reference": 0.10},
    "not_as_described": {"order_record": 0.15, "listing_description": 0.30, "item_photos": 0.20,
                         "buyer_communication": 0.20, "shipping_tracking": 0.05,
                         "delivery_confirmation": 0.05, "policy_reference": 0.05},
    "unknown": {"order_record": 0.30, "buyer_communication": 0.20},
}


class EtsyAdapter:
    name = "etsy"
    response_window_days = 7  # PLACEHOLDER, unverified
    verified = False

    def checklist(self, dispute_type):
        return CHECKLISTS.get(dispute_type, CHECKLISTS["unknown"])

    def central_question(self, dispute_type):
        return {"not_received": "Did the item reach the buyer?",
                "not_as_described": "Did the item match what the listing described?"}.get(dispute_type, "What does the record show?")

    def weights(self, dispute_type):
        return WEIGHTS.get(dispute_type, WEIGHTS["unknown"])

    @staticmethod
    def _date(value, field):
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"notice.txt '{field}' must be a date like 2026-03-10, got '{value}'") from None

    def deadline(self, dispute):
        n = dispute.notice
        if n.deadline:
            return self._date(n.deadline, "deadline")
        if n.opened:
            return self._date(n.opened, "opened") + timedelta(days=self.response_window_days)
        return None

    def warnings(self):
        return ["Etsy response window and checklist are unverified placeholders; confirm against current Etsy seller policy."]
