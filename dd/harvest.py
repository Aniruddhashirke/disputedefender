"""Collect verified model letters as training pairs (prompt, completion).

Only a FIRST draft that passed the verifier untouched is kept, so every completion is something the model wrote itself
and the verifier approved, and its prompt is exactly the one the model sees at inference. Retried or repaired letters
are skipped because their prompts (which contain a previous draft) or their text (edited by code) do not match.
Resumable: cases already in the output file are skipped, so Ctrl+C or a sleeping laptop loses nothing."""
import json
import os
import time

from .draft import BackendError
from .evidence import build_evidence, classify
from .parse import load_case
from .pipeline import run_case, suggest_exclusions


def _done(path):
    names = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    names.add(json.loads(line)["case"])
                except (ValueError, KeyError):
                    pass
    return names


def harvest(cases_dir, backend, out_path, today=None, photos="code", prompt_style="full", limit=None, progress=None, max_consecutive_errors=3):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    seen = _done(out_path)
    stats = {"kept": 0, "skipped_not_clean_first_draft": 0, "skipped_fold": 0, "already_done": 0, "errors": 0, "interrupted": False}
    names = [n for n in sorted(os.listdir(cases_dir)) if os.path.exists(os.path.join(cases_dir, n, "notice.txt"))]
    started, consecutive, attempted = time.perf_counter(), 0, 0
    try:
        for name in names:
            if name in seen:
                stats["already_done"] += 1
                continue
            if limit is not None and stats["kept"] >= limit:
                break
            dispute = load_case(os.path.join(cases_dir, name))
            exclude = suggest_exclusions(build_evidence(dispute, classify(dispute)))
            try:
                r = run_case(dispute, backend, today=today, exclude=exclude, photos=photos, prompt_style=prompt_style)
            except BackendError as e:
                stats["errors"] += 1
                consecutive += 1
                if progress:
                    progress(f"{name}: ERROR {str(e)[:150]}")
                if consecutive >= max_consecutive_errors:
                    raise
                continue
            attempted += 1
            if r.triage.verdict != "fight":
                stats["skipped_fold"] += 1
                continue
            consecutive = 0
            first = r.history[0]
            if first["issues"]:
                stats["skipped_not_clean_first_draft"] += 1
                note = "first draft not clean, skipped"
            else:
                with open(out_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"case": name, "model": getattr(backend, "model", None) or backend.name,
                                        "dispute_type": r.dispute_type, "prompt": first["prompt"],
                                        "prompt_short": first.get("prompt_short"),
                                        "completion": first["draft"]}) + "\n")
                stats["kept"] += 1
                note = "kept"
            if progress:
                elapsed = time.perf_counter() - started
                progress(f"{name}: {note} | kept {stats['kept']} | elapsed {int(elapsed // 60)}m{int(elapsed % 60):02d}s")
    except KeyboardInterrupt:
        stats["interrupted"] = True
    except BackendError:
        stats["interrupted"] = True
        if progress:
            progress(f"stopped after {max_consecutive_errors} errors in a row. Is Ollama running?")
    return stats
