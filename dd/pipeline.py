from dataclasses import asdict
from datetime import date

from .adapters import get_adapter
from .draft import MockBackend, build_prompt
from .evidence import build_evidence, classify, find_gaps
from .models import Result
from .triage import triage as run_triage
from .verify import CITE, PHOTO_KINDS, canonicalize, repair_letter, verify_letter


CLOSING_ASK = "I ask that this case be resolved in the seller's favor based on the evidence above."
THANKS = "Thank you for reviewing this case."
SENSITIVE = ("refund", "replacement", "replace", "sorry", "my fault", "mistake", "apolog")


def ensure_closing(letter):
    """Models often drop the closing request. It is boilerplate with no factual claim, so add it deterministically."""
    lines = [l for l in letter.splitlines() if l.strip()]
    if not lines or not lines[0].lower().startswith(("hello", "dear", "hi")):
        lines.insert(0, "Hello,")
    body = [l for l in lines if l.strip().lower().rstrip(",") != "sincerely"]
    if not any(l.lower().startswith("i ask that this case be resolved") for l in body):
        body.append(CLOSING_ASK)
    if not any(l.lower().startswith("thank you") for l in body):
        body.append(THANKS)
    body.append("Sincerely,")
    return "\n".join(body)


BUYER_DEMANDS = ("refund", "chargeback", "money back")


def suggest_exclusions(evidence):
    """Messages that usually hurt the seller if quoted: the seller's own refund offers or apologies,
    and the buyer's demands. The seller decides; nothing is removed without being asked."""
    out = []
    for e in evidence:
        if e.kind != "buyer_communication":
            continue
        t, sender = e.text.lower(), e.fields.get("sender")
        if (sender == "seller" and any(k in t for k in SENSITIVE)) or (sender == "buyer" and any(k in t for k in BUYER_DEMANDS)):
            out.append(e.id)
    return out


def key_evidence_ids(evidence, weights, n=3):
    """The evidence that matters most for this dispute type, from the same weights used in triage."""
    ranked = sorted((e for e in evidence if weights.get(e.kind, 0) > 0), key=lambda e: (-weights[e.kind], e.id))
    return [e.id for e in ranked[:n]]


def add_photo_lines(letter, photo_ev):
    """Photos are file names only, so code writes their sentences. The model never sees them and cannot embellish."""
    new = [f"A photo file named {e.fields.get('filename', '')} is on file [{e.id}]." for e in photo_ev]
    lines = letter.splitlines()
    idx = next((i for i, l in enumerate(lines) if l.strip().lower().startswith(("i ask that this case", "thank you", "sincerely"))), len(lines))
    return "\n".join(lines[:idx] + new + lines[idx:])


def clean_draft(text):
    """Drop markdown code fences some models wrap around their answer, and undo harmless formatting habits
    (bold markers, bullets, [E1, E2] citations). The result is also what gets saved as a training completion."""
    return canonicalize("\n".join(l for l in text.splitlines() if not l.strip().startswith("```")))


def _deadline_text(deadline, days_left):
    if days_left is None:
        return "DEADLINE: unknown"
    if days_left < 0:
        return f"DEADLINE: {deadline} (passed {-days_left} day{'s' if days_left != -1 else ''} ago)"
    if days_left == 0:
        return f"DEADLINE: {deadline} (due today)"
    return f"DEADLINE: {deadline} ({days_left} day{'s' if days_left != 1 else ''} left)"


def run_case(dispute, backend, today=None, max_attempts=2, force=False, exclude=None, photos="code", prompt_style="full"):
    """parse (done by caller) -> classify -> evidence -> gaps -> triage -> draft -> verify."""
    today = today or date.today()
    max_attempts = max(1, int(max_attempts))
    adapter = get_adapter(dispute.platform)
    dtype = classify(dispute)
    evidence = build_evidence(dispute, dtype)
    gaps = find_gaps(evidence, adapter.checklist(dtype))
    amount = dispute.notice.amount or dispute.order.amount
    tri = run_triage(dtype, evidence, amount, adapter.weights(dtype))

    deadline = adapter.deadline(dispute)
    days_left = (deadline - today).days if deadline else None
    warnings = list(adapter.warnings())
    if not getattr(backend, "local", True):
        warnings.append("The model server is not on this machine. Customer data in this case is sent to it.")
    if days_left is not None and days_left < 0:
        warnings.append(f"Deadline passed {-days_left} day(s) ago; check whether the platform still accepts a response.")

    excluded = sorted(set(exclude or []))
    unknown = [x for x in excluded if x not in {e.id for e in evidence}]
    if unknown:
        raise ValueError(f"cannot exclude unknown evidence id(s): {', '.join(unknown)}")
    letter_evidence = [e for e in evidence if e.id not in excluded]
    suggested = [x for x in suggest_exclusions(evidence) if x not in excluded]

    if photos not in ("code", "model"):
        raise ValueError("photos must be 'code' or 'model'")
    if prompt_style not in ("full", "short"):
        raise ValueError("prompt_style must be 'full' or 'short'")
    photo_ev = [e for e in letter_evidence if e.kind in PHOTO_KINDS] if photos == "code" else []
    photo_ids = {e.id for e in photo_ev}
    model_evidence = [e for e in letter_evidence if e.id not in photo_ids]  # what the model is shown and checked against
    key_ids = key_evidence_ids(model_evidence, adapter.weights(dtype))
    short_prompt = build_prompt(dtype, model_evidence, None, key_ids, adapter.central_question(dtype), None, "short")
    letter, issues, attempts, degraded, history, repairs = None, [], 0, False, [], []
    if tri.verdict == "fight" or force:
        feedback, previous, best = None, None, None
        while attempts < max_attempts:
            attempts += 1
            prompt = build_prompt(dtype, model_evidence, feedback, key_ids, adapter.central_question(dtype), previous, prompt_style)
            draft = clean_draft(backend.generate(prompt, model_evidence, dtype, feedback))
            found, _ok = verify_letter(draft, model_evidence, require_facts=True)
            stats = getattr(backend, "last_stats", None)
            history.append({"attempt": attempts, "prompt": prompt, "prompt_short": short_prompt, "draft": draft,
                            "issues": [asdict(i) for i in found], "stats": dict(stats) if stats else None})
            if stats and stats.get("truncated") and not any("length limit" in w for w in warnings):
                warnings.append("The model hit its length limit, so a draft may be cut off (raise DD_NUM_PREDICT if this keeps happening).")
            if not found:
                letter, issues = draft, []
                break
            if best is None or len(found) < len(best[2]):
                best = (attempts, draft, found)
            _fixed, _notes = repair_letter(draft, model_evidence)
            if not any(n.startswith("dropped") for n in _notes):
                break  # code can repair this without losing anything (photo clause, misplaced citation); a retry only costs time
            feedback, previous = found, draft
        if letter is None:
            _n, draft, issues = best
            letter, repairs = repair_letter(draft, model_evidence)
            if not any(CITE.search(l) for l in letter.splitlines()):
                letter = MockBackend().generate("", model_evidence, dtype)
                repairs.append("model output had nothing usable; used the evidence-only template instead")
            still, _ok = verify_letter(letter, model_evidence, require_facts=True)
            if still:  # repair must never ship something the verifier rejects
                letter = "\n".join(l for l in letter.splitlines() if l not in {i.line for i in still})
            degraded = any(r.startswith(("dropped", "model output")) for r in repairs)
            warnings.append(f"The model's draft failed verification after {attempts} attempt(s). The code repaired it ({len(repairs)} change(s), listed under REPAIRS). Review before sending.")
    if letter is not None:
        if photo_ev:
            letter = add_photo_lines(letter, photo_ev)
        letter = ensure_closing(letter)
        if suggested:
            warnings.append(f"Consider leaving out {', '.join(suggested)}: refund offers, apologies and buyer demands can weaken the seller's position. Re-run with --exclude {','.join(suggested)}.")
    return Result(platform=adapter.name, dispute_type=dtype, deadline=deadline.isoformat() if deadline else None,
                  days_left=days_left, triage=tri, evidence=evidence, gaps=gaps, letter=letter,
                  issues=issues, attempts=attempts, degraded=degraded, warnings=warnings,
                  excluded=excluded, suggested_exclusions=suggested, repairs=repairs, history=history)


def render_packet(r, show_drafts=False):
    out = [f"PLATFORM: {r.platform}    TYPE: {r.dispute_type}",
           _deadline_text(r.deadline, r.days_left),
           f"VERDICT: {r.triage.verdict.upper()}  (win score {r.triage.win_likelihood:.2f}, expected ${r.triage.expected_recovery:.2f})",
           f"  {r.triage.reason}", "", "EVIDENCE"]
    out += [f"  [{e.id}] {e.text}" + ("   (left out of letter)" if e.id in r.excluded else "") for e in r.evidence]
    out += ["", "GAPS" + ("" if r.gaps else ": none")]
    out += [f"  - {g.label}: {g.how}" for g in r.gaps]
    if r.letter:
        tag = " (REPAIRED BY CODE, parts removed; see REPAIRS)" if r.degraded else (" (REPAIRED BY CODE; see REPAIRS)" if r.repairs else " (verified: every clause cites evidence)")
        out += ["", "LETTER" + tag, r.letter]
        if r.repairs:
            out += ["", "REPAIRS"] + [f"  ~ {x}" for x in r.repairs]
    elif r.triage.verdict == "fold":
        out += ["", "No letter drafted (verdict is fold). Use --force to draft anyway."]
    if r.issues:
        out += ["", "VERIFIER FLAGGED THESE LINES IN THE MODEL DRAFT:"]
        for i in r.issues:
            out += [f"  x {i.problem}", f"      {i.line}"]
    if show_drafts:
        for h in r.history:
            out += ["", f"--- RAW DRAFT, ATTEMPT {h['attempt']} ({len(h['issues'])} problem(s)) ---", h["draft"]]
            out += [f"  x {x['problem']}  <- {x['line'][:90]}" for x in h["issues"]]
    if r.warnings:
        out += ["", "WARNINGS"] + [f"  ! {w}" for w in r.warnings]
    return "\n".join(out)
