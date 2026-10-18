"""One command that checks everything the local model needs and says what to do about whatever is wrong."""
import json
import urllib.request

from .draft import BackendError, OllamaBackend

TYPICAL_LETTER_TOKENS = 350


def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def check_ollama(url=None, model=None, timeout=120):
    """Returns (ok, lines). Each line starts with OK, WARN, FAIL or INFO."""
    b = OllamaBackend(url=url, model=model, timeout=timeout)
    lines = []
    installed = b._installed()
    if installed is None:
        return False, [f"FAIL  Cannot reach Ollama at {b.url}.",
                       "      Start it: open the Ollama app from the Start menu (a llama icon appears in the system tray),",
                       "      or run `ollama serve` in a separate terminal and leave that window open. Then run this again."]
    lines.append(f"OK    Ollama is running at {b.url}. Installed models: {', '.join(installed) or 'none'}.")
    if not installed:
        return False, lines + ["FAIL  No models installed. Run: ollama pull gemma3:4b"]
    try:
        b._resolve_model()
    except BackendError as e:
        return False, lines + [f"FAIL  {e}"]
    lines.append(f"OK    Model in use: {b.model}" + ("" if b.local else "   (WARN: the server is not on this machine)"))
    try:
        b.generate("Reply with the single word: ready", [], "check")
    except BackendError as e:
        return False, lines + [f"FAIL  A test question failed: {e}"]
    st = b.last_stats or {}
    tps, first = st.get("tokens_per_second"), st.get("first_token_seconds")
    lines.append(f"OK    Answered a test question: first word after {first} s, {tps} tokens per second.")
    try:
        for m in _get(f"{b.url}/api/ps").get("models", []):
            size, vram = m.get("size") or 0, m.get("size_vram") or 0
            pct = round(100 * vram / size) if size else 0
            where = "100% CPU (no graphics chip is being used)" if pct == 0 else f"{pct}% on the graphics chip, {100 - pct}% on the CPU"
            lines.append(f"INFO  {m.get('name')}: {size / 1e9:.1f} GB, {where}.")
    except Exception:
        lines.append("INFO  Could not read where the model is running (older Ollama).")
    if tps:
        est = round((first or 0) + TYPICAL_LETTER_TOKENS / tps)
        speed = "fast" if tps >= 15 else "usable" if tps >= 6 else "slow" if tps >= 3 else "very slow"
        lines.append(f"{'OK  ' if tps >= 6 else 'WARN'}  Speed is {speed}. A typical letter (about {TYPICAL_LETTER_TOKENS} tokens) should take about {est} seconds, plus time to read the prompt.")
        if tps < 6:
            lines.append("      To speed up: plug in the laptop, use its best-performance power mode, close other programs,")
            lines.append("      try a smaller model (--model gemma3:1b), or run the long jobs on a computer with a GPU.")
    return True, lines
