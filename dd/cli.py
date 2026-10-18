import argparse
import json
import os
import sys
from datetime import date

from .draft import BackendError, get_backend
from .parse import load_case
from .evalrun import evaluate, render_eval, render_report, save
from .pipeline import render_packet, run_case


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve(path):
    """Accept case paths relative to the project folder even when run from another folder."""
    if os.path.exists(path):
        return path
    alt = os.path.join(ROOT, path)
    return alt if os.path.exists(alt) else path


def _cases_dir(path):
    """A folder of case folders. Explains what to do instead of showing a traceback."""
    base = _resolve(path)
    if not os.path.isdir(base):
        raise ValueError(f"folder not found: {path}. Make test cases first with: python -m dd generate {path} --count 20 --seed 2")
    if not any(os.path.exists(os.path.join(base, n, "notice.txt")) for n in os.listdir(base)):
        raise ValueError(f"no case folders (each needs a notice.txt) found in {path}")
    return base


def _today(s):
    return date.fromisoformat(s) if s else None


def main(argv=None):
    from . import __version__
    ap = argparse.ArgumentParser(prog="dd", description="Dispute Defender")
    ap.add_argument("--version", action="version", version=f"dd {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "batch", "eval"):
        p = sub.add_parser(name)
        p.add_argument("path")
        p.add_argument("--backend", default="mock", choices=["mock", "ollama"])
        p.add_argument("--model", help="Ollama model name, e.g. gemma3:4b (default: DD_MODEL, else first installed Gemma)")
        p.add_argument("--photos", default="code", choices=["code", "model"], help="code (default): code writes the photo sentences; model: the model writes them")
        p.add_argument("--prompt-style", default="full", choices=["full", "short"], help="full: all rules and an example; short: minimal prompt for a fine-tuned model")
        p.add_argument("--timeout", type=float, help="seconds to wait for each model call (default 300, or DD_TIMEOUT)")
        p.add_argument("--today", help="override today's date (YYYY-MM-DD)")
        p.add_argument("--max-attempts", type=int, default=2)
        if name == "eval":
            p.add_argument("--repeat", type=int, default=3, help="runs per case (models vary run to run)")
            p.add_argument("--auto-exclude", action="store_true", help="leave out suggested refund messages, as a seller would")
            p.add_argument("--out", help="save full results as JSON, e.g. results_gemma.json")
            p.add_argument("--limit", type=int, help="only the first N cases (quick check on a slow machine)")
        if name == "run":
            p.add_argument("--json", action="store_true")
            p.add_argument("--force", action="store_true", help="draft even if verdict is fold")
            p.add_argument("--show-drafts", action="store_true", help="print every raw model draft and what the verifier flagged")
            p.add_argument("--exclude", default="", help="evidence ids to leave out of the letter, e.g. E7,E8")
    g = sub.add_parser("generate", help="create synthetic dispute cases for evaluation and training")
    g.add_argument("out_dir")
    g.add_argument("--count", type=int, default=120)
    g.add_argument("--seed", type=int, default=20261013)
    h = sub.add_parser("harvest", help="collect first-draft-verified model letters as training pairs (resumable)")
    h.add_argument("path", help="folder of cases, e.g. generated_cases")
    h.add_argument("--out", default="training/harvest.jsonl")
    h.add_argument("--backend", default="ollama", choices=["mock", "ollama"])
    h.add_argument("--model")
    h.add_argument("--timeout", type=float)
    h.add_argument("--today")
    h.add_argument("--photos", default="code", choices=["code", "model"])
    h.add_argument("--prompt-style", default="full", choices=["full", "short"])
    h.add_argument("--limit", type=int, help="stop after keeping this many letters")
    dr = sub.add_parser("doctor", help="check that Ollama and the model are working, and how fast")
    dr.add_argument("--model")
    dr.add_argument("--timeout", type=float, default=120)
    rp = sub.add_parser("report", help="show the lines the verifier flagged and the repairs made in a saved eval file")
    rp.add_argument("file")
    a = ap.parse_args(argv)
    if a.cmd == "doctor":
        from .doctor import check_ollama
        ok, lines = check_ollama(model=a.model, timeout=a.timeout)
        print("\n".join(lines))
        return 0 if ok else 2
    if a.cmd == "report":
        try:
            with open(a.file, encoding="utf-8") as f:
                print(render_report(json.load(f)))
        except (OSError, ValueError, KeyError) as e:
            print(f"error: cannot read {a.file}: {e}", file=sys.stderr)
            return 2
        return 0
    if a.cmd == "harvest":
        from .harvest import harvest
        try:
            stats = harvest(_cases_dir(a.path), get_backend(a.backend, a.model, a.timeout), a.out, today=_today(a.today),
                            photos=a.photos, prompt_style=a.prompt_style, limit=a.limit, progress=lambda t: print(t, file=sys.stderr, flush=True))
        except (ValueError, BackendError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(json.dumps(stats, indent=2))
        print(f"training pairs are in {a.out}")
        return 0
    if a.cmd == "generate":
        from .generate import generate_cases
        try:
            names = generate_cases(a.out_dir, a.count, a.seed)
        except FileExistsError as e:
            print(f"error: {e.filename or a.out_dir} already exists. Choose a new folder or delete the old one.", file=sys.stderr)
            return 2
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"created {len(names)} synthetic cases in {a.out_dir} (seed {a.seed})")
        return 0
    backend = get_backend(a.backend, a.model, a.timeout)
    try:
        if a.cmd == "run":
            r = run_case(load_case(_resolve(a.path)), backend, today=_today(a.today), max_attempts=a.max_attempts, force=a.force, photos=a.photos, prompt_style=a.prompt_style,
                         exclude=[x.strip().upper() for x in a.exclude.split(",") if x.strip()])
            print(json.dumps(r.to_dict(), indent=2) if a.json else render_packet(r, a.show_drafts))
            return 0
        if a.cmd == "eval":
            res = evaluate(_cases_dir(a.path), backend, repeat=max(1, a.repeat), today=_today(a.today),
                           auto_exclude=a.auto_exclude, max_attempts=a.max_attempts, photos=a.photos, prompt_style=a.prompt_style, limit=a.limit,
                           progress=lambda t: print(t, file=sys.stderr, flush=True),
                           on_update=(lambda snap: save(snap, a.out)) if a.out else None)
            print(render_eval(res))
            if a.out:
                save(res, a.out)
                print(f"\nsaved {a.out}")
            return 0
        rows = []
        base = _cases_dir(a.path)
        for name in sorted(os.listdir(base)):
            d = os.path.join(base, name)
            if os.path.isdir(d) and os.path.exists(os.path.join(d, "notice.txt")):
                r = run_case(load_case(d), backend, today=_today(a.today), max_attempts=a.max_attempts, photos=a.photos, prompt_style=a.prompt_style)
                rows.append((name, r.dispute_type, r.triage.verdict, r.triage.win_likelihood, len(r.gaps),
                             "-" if r.letter is None else ("degraded" if r.degraded else "verified")))
        print(f"{'case':28} {'type':17} {'verdict':8} {'score':6} {'gaps':5} letter")
        for row in rows:
            print(f"{row[0]:28} {row[1]:17} {row[2]:8} {row[3]:<6.2f} {row[4]:<5} {row[5]}")
        return 0
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except BackendError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
