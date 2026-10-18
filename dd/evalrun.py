"""Score a model on a folder of cases: how often is the FIRST draft clean, how often does the code have to repair it,
and what kinds of mistakes does the model make. Run the same command on base and fine-tuned models to compare them."""
import json
import os
import time
from collections import Counter

from .draft import BackendError
from .parse import load_case
from .pipeline import run_case, suggest_exclusions
from .evidence import build_evidence, classify

CATEGORIES = [
    ("photo described", "photo claim"), ("no evidence citation", "uncited line"),
    ("after the last citation", "uncited text"), ("quoted text", "quote"), ("strong claim", "overclaim word"),
    ("number or date", "number or date"), ("date not in", "date"), ("id not in", "id"),
    ("name or proper noun", "name"), ("evidence ids must", "evidence id in prose"),
    ("photo evidence must", "photo claim"), ("unknown evidence", "unknown citation"), ("no cited facts", "empty letter"),
]


def categorize(problem):
    for key, cat in CATEGORIES:
        if key in problem:
            return cat
    return "other"


def _summarize(rows, skipped, errors, interrupted):
    n = len(rows)
    probs = Counter(c for row in rows for c in row["first_draft_problems"])
    rate = lambda key: round(sum(bool(r[key]) for r in rows) / n, 2) if n else None
    return {
        "runs": n, "errors": errors, "interrupted": interrupted, "cases_skipped_as_fold": sorted(set(skipped)),
        "first_draft_clean_rate": rate("first_draft_clean"), "repaired_rate": rate("repaired"),
        "content_dropped_rate": rate("dropped_content"),
        "avg_attempts": round(sum(r["attempts"] for r in rows) / n, 2) if n else None,
        "avg_seconds": round(sum(r["seconds"] for r in rows) / n, 1) if n else None,
        "avg_tokens_per_second": _avg(rows, "tokens_per_second"), "avg_first_token_seconds": _avg(rows, "first_token_seconds"),
        "first_draft_problem_runs": dict(probs.most_common()),
    }


def _avg(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _mins(seconds):
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def evaluate(cases_dir, backend, repeat=3, today=None, auto_exclude=False, max_attempts=2, photos="code", prompt_style="full",
             progress=None, on_update=None, max_consecutive_errors=3, limit=None):
    """progress(text) is called after every run; on_update(result) after every run so partial results can be saved.
    Ctrl+C stops cleanly and returns what has been measured so far."""
    jobs = []
    for name in sorted(os.listdir(cases_dir)):
        d = os.path.join(cases_dir, name)
        if os.path.isdir(d) and os.path.exists(os.path.join(d, "notice.txt")):
            jobs.append((name, load_case(d)))
    jobs = jobs[:limit] if limit else jobs
    rows, skipped, errors, consecutive, done, interrupted = [], [], 0, 0, 0, False
    total = len(jobs) * repeat
    started = time.perf_counter()

    def snapshot():
        from . import __version__
        return {"version": __version__, "model": getattr(backend, "model", None) or backend.name, "backend": backend.name, "repeat": repeat,
                "photos": photos, "prompt_style": prompt_style, "summary": _summarize(rows, skipped, errors, interrupted), "rows": rows}

    try:
        for name, dispute in jobs:
            exclude = suggest_exclusions(build_evidence(dispute, classify(dispute))) if auto_exclude else []
            for i in range(repeat):
                t0 = time.perf_counter()
                try:
                    r = run_case(dispute, backend, today=today, max_attempts=max_attempts, exclude=exclude, photos=photos, prompt_style=prompt_style)
                except BackendError as e:
                    errors += 1
                    consecutive += 1
                    done += 1
                    if progress:
                        progress(f"[{done}/{total}] {name} run {i + 1}: ERROR {str(e)[:160]}")
                    if consecutive >= max_consecutive_errors:
                        raise
                    continue
                secs = time.perf_counter() - t0
                done += 1
                if r.triage.verdict != "fight":
                    skipped.append(name)
                    done += repeat - i - 1  # the remaining runs of a folded case are not needed
                    if progress:
                        progress(f"[{done}/{total}] {name}: verdict is fold, not scored")
                    break
                consecutive = 0  # only a real model call proves the server is answering; folds never call it
                first = r.history[0]["issues"] if r.history else []
                st = (r.history[0].get("stats") or {}) if r.history else {}
                rows.append({"case": name, "run": i + 1, "attempts": r.attempts, "first_draft_clean": not first,
                             "repaired": bool(r.repairs), "dropped_content": r.degraded, "seconds": round(secs, 1),
                             "first_draft_problems": sorted({categorize(x["problem"]) for x in first}),
                             "repairs": r.repairs, "tokens": st.get("tokens"), "first_token_seconds": st.get("first_token_seconds"),
                             "tokens_per_second": st.get("tokens_per_second"),
                             "first_draft_issues": [{"problem": x["problem"], "line": x["line"][:200]} for x in first]})
                if progress:
                    elapsed = time.perf_counter() - started
                    left = (elapsed / done) * (total - done) if done else 0
                    state = "clean" if not first else "problems: " + ", ".join(rows[-1]["first_draft_problems"])
                    progress(f"[{done}/{total}] {name} run {i + 1}: {secs:.0f}s, {state} | elapsed {_mins(elapsed)}, about {_mins(left)} left")
                if on_update:
                    on_update(snapshot())
    except KeyboardInterrupt:
        interrupted = True
    except BackendError:
        interrupted = True
        if progress:
            progress(f"stopped after {max_consecutive_errors} errors in a row. Is Ollama running? Partial results follow.")
    return snapshot()


def render_eval(result):
    s = result["summary"]
    out = [f"MODEL: {result['model']}  (backend {result['backend']}, {result['repeat']} run(s) per case, photos by {result.get('photos', 'model')}, {result.get('prompt_style', 'full')} prompt)",
           f"runs scored: {s['runs']}    errors: {s.get('errors', 0)}    skipped as fold: {', '.join(s['cases_skipped_as_fold']) or 'none'}"]
    if s.get("interrupted"):
        out.append("NOTE: stopped early; these are partial results.")
    if not s["runs"]:
        return "\n".join(out + ["nothing to score"])
    pct = lambda x: f"{round(x * 100)}%"
    out += [f"first draft clean:     {pct(s['first_draft_clean_rate'])}",
            f"needed code repair:    {pct(s['repaired_rate'])}",
            f"content had to be dropped: {pct(s['content_dropped_rate'])}",
            f"average attempts:      {s['avg_attempts']}",
            f"average seconds/case:  {s['avg_seconds']}"]
    if s.get("avg_tokens_per_second") is not None:
        out.append(f"speed:                 {s['avg_tokens_per_second']} tokens/s, first token after {s['avg_first_token_seconds']} s")
    out += ["", "first-draft problems (runs affected):"]
    out += [f"  {k}: {v}" for k, v in s["first_draft_problem_runs"].items()] or ["  none"]
    out += ["", f"{'case':28} run attempts clean repaired secs  problems"]
    for r in result["rows"]:
        out.append(f"{r['case']:28} {r['run']:<3} {r['attempts']:<8} {str(r['first_draft_clean']):6} {str(r['repaired']):8} {r['seconds']:<5} {', '.join(r['first_draft_problems']) or '-'}")
    return "\n".join(out)


def save(result, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


def render_report(result):
    """The summary plus every line the verifier flagged and every change the repair made, for reading failures."""
    out = [render_eval(result), "", "WHAT WENT WRONG, RUN BY RUN"]
    shown = False
    for r in result["rows"]:
        issues, repairs = r.get("first_draft_issues"), r.get("repairs") or []
        if not issues and not repairs:
            continue
        shown = True
        out.append(f"\n{r['case']} run {r['run']} (attempts {r['attempts']})")
        for i in issues or []:
            out.append(f"  flagged: {i['problem']}\n           {i['line']}")
        for n in repairs:
            out.append(f"  repair:  {n}")
    if not shown:
        out.append("  nothing was flagged or repaired in the saved runs (or this file was saved by an older version that did not keep the lines)")
    return "\n".join(out)
