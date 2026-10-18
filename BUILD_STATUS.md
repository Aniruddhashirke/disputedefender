# Build status

| Area | Status |
|---|---|
| Case parsing (notice, order CSV, messages, photos, policy, listing); CRLF, UTF-16, ANSI; clear errors | done, tested |
| Evidence builder with ids and per-type gap finder | done, tested |
| Fight-or-fold triage (explainable heuristic) | done, tested |
| Etsy adapter (not received, not as described) | done with PLACEHOLDER policy and window |
| Clause-level fail-closed verifier | done, tested |
| Retry with previous draft, then deterministic repair (citation fix, near-miss quote fix, emphasis removal, photo rewrite), then evidence-only template | done; fuzzed on 2,000+ corrupted letters, repair never added a word |
| Formatting cleanup (bullets, bold, [E1, E2] citations) and tolerant quote check | done, tested |
| Mock drafter (offline) | done |
| Ollama / Gemma backend with auto model pick, clear errors, locality warning | run on real local Gemma (cases 001, 004, 005) |
| CLI (run, batch, --exclude, --model, --show-drafts) and launchers | done |
| Thin JSON API (CORS for localhost only, size limit) | done, standard library |
| Synthetic sample cases | 5 |
| Tests | 163 passing, includes real Gemma drafts as fixtures |
| React UI, platform switcher | not started |
| Temporal workflow | not started |
| Sentry tracing | not started |
| Eval command (first-draft clean rate, repair rate, mistake types; progress, partial saves, Ctrl+C, timeouts) | done; two real Gemma runs saved in results/ |
| Short prompt style (--prompt-style short) and harvest records both prompts | done, tested offline |
| Streaming Ollama calls: stall detection, total cap, tokens/s and first-token time, truncation warning; `report` and `--limit` for eval | done, tested against a stub server |
| `doctor` command: Ollama reachable, model installed, speed, GPU or CPU, time per letter | done, tested against a stub server |
| Unsupported apologies and admissions rejected; common capitalised words are not treated as names | done, tested |
| Photo sentences written by code (model never sees photo evidence) | done, tested offline; not yet measured on real Gemma |
| Synthetic case generator (python -m dd generate) | realistic and varied (types, evidence, refund messages, about 20% deliberate folds) |
| Harvest command: verified first-draft letters as training pairs (resumable) | done, tested offline |
| Tinker fine-tune | not started |
| Evidence Vault, Slow-Week tab, voice, deploy, real-user test | not started |
