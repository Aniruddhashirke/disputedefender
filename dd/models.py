from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Notice:
    type_raw: str = ""
    reason: str = ""
    amount: float = 0.0
    opened: str = ""
    deadline: str = ""


@dataclass
class Order:
    order_id: str = ""
    buyer_name: str = ""
    item: str = ""
    amount: float = 0.0
    order_date: str = ""
    ship_date: str = ""
    carrier: str = ""
    tracking_number: str = ""
    delivery_status: str = ""
    delivery_date: str = ""


@dataclass
class Message:
    ts: str
    sender: str
    text: str


@dataclass
class Dispute:
    platform: str
    notice: Notice
    order: Order
    messages: list = field(default_factory=list)
    photos: list = field(default_factory=list)
    policy_text: str = ""
    listing_text: str = ""


@dataclass
class Evidence:
    id: str
    kind: str
    text: str
    fields: dict = field(default_factory=dict)


@dataclass
class Gap:
    slot: str
    label: str
    how: str


@dataclass
class Triage:
    dispute_type: str
    win_likelihood: float
    expected_recovery: float
    verdict: str
    reason: str


@dataclass
class Issue:
    line: str
    problem: str


@dataclass
class Result:
    platform: str
    dispute_type: str
    deadline: Optional[str]
    days_left: Optional[int]
    triage: Triage
    evidence: list
    gaps: list
    letter: Optional[str]
    issues: list
    attempts: int
    degraded: bool
    warnings: list
    excluded: list = field(default_factory=list)
    suggested_exclusions: list = field(default_factory=list)
    repairs: list = field(default_factory=list)  # what the code changed in the draft, if anything
    history: list = field(default_factory=list)  # every draft attempt and what the verifier said about it

    def to_dict(self):
        return asdict(self)
