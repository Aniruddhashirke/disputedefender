from .models import Triage

MIN_EXPECTED_RECOVERY = 5.0   # dollars; below this, fighting is not worth the seller's time
MIN_LIKELIHOOD = 0.25


def triage(dispute_type, evidence, amount, weights):
    """Fight-or-fold. Likelihood is a transparent heuristic (weights per evidence slot),
    NOT a calibrated probability. It exists to give honest, explainable advice."""
    have = {e.kind for e in evidence}
    likelihood = round(min(1.0, sum(w for slot, w in weights.items() if slot in have)), 2)
    expected = round(likelihood * amount, 2)
    missing = [s for s in weights if s not in have]
    if likelihood < MIN_LIKELIHOOD:
        verdict = "fold"
        reason = f"Evidence is too thin to support a win (score {likelihood:.2f}). Missing: {', '.join(missing) or 'none'}. Add evidence and re-run, or let it go."
    elif expected < MIN_EXPECTED_RECOVERY:
        verdict = "fold"
        reason = f"Expected recovery is only ${expected:.2f} (score {likelihood:.2f} on ${amount:.2f}); likely not worth the time."
    else:
        verdict = "fight"
        reason = f"Worth fighting: score {likelihood:.2f}, expected recovery ${expected:.2f}."
        if missing:
            reason += f" Filling these would help: {', '.join(missing)}."
    return Triage(dispute_type=dispute_type, win_likelihood=likelihood, expected_recovery=expected, verdict=verdict, reason=reason)
