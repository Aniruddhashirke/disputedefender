import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

ORDER = ["dispute_notice", "order_record", "shipping_tracking", "delivery_confirmation",
         "shipping_proof_photo", "item_photos", "listing_description", "buyer_communication", "policy_reference"]


class BackendError(Exception):
    pass


EXAMPLE = """Hello,
The buyer says the mug arrived chipped [E1]. The listing stated that each mug is hand-glazed and may show small variations [E4].
The order shipped on 2026-01-10 via UPS [E2], and the carrier shows it delivered on 2026-01-13 [E3].
On 2026-01-14 the buyer wrote: "It has a small chip" [E5].
I ask that this case be resolved in the seller's favor based on the evidence above.
Thank you for reviewing this case.
Sincerely,"""


PHOTO_RULES = (
    "- PHOTOS ARE NOT VISUAL EVIDENCE: never say what a photo shows, proves, depicts, contains, or when it was taken. "
    "The ONLY allowed photo sentence is exactly: A photo file named <exact file name> is on file [E#].\n"
    "- If a photo claim cannot follow that exact filename-only rule, delete the claim rather than describing the image.\n"
)
PHOTO_KINDS = ("shipping_proof_photo", "item_photos")


def _short_prompt(dispute_type, evidence, issues, key_ids, question, previous):
    """A minimal prompt for a model that was fine-tuned on the rules, so they no longer need to be spelled out."""
    ev = "\n".join(f"[{e.id}] ({e.kind}) {e.text}" for e in evidence)
    p = ("Write a polite seller response letter. Cite evidence like [E3] after each fact. Use only the evidence. "
         "Start with 'Hello,' and end with 'Sincerely,'.\n"
         f"Dispute type: {dispute_type}\n")
    if question:
        p += f"Question: {question}\n"
    p += f"Evidence:\n{ev}\n"
    if key_ids:
        p += f"Key evidence: {', '.join(key_ids)}\n"
    if issues:
        p += "Problems in the previous draft:\n" + "\n".join(f"- {i.line} -> {i.problem}" for i in issues)
        if previous:
            p += "\nPrevious draft:\n" + previous + "\nReturn the corrected letter."
    return p


def build_prompt(dispute_type, evidence, issues=None, key_ids=None, question="", previous=None, style="full"):
    if style == "short":
        return _short_prompt(dispute_type, evidence, issues, key_ids, question, previous)
    has_photos = any(e.kind in PHOTO_KINDS for e in evidence)
    ev = "\n".join(f"[{e.id}] ({e.kind}) {e.text}" for e in evidence)
    p = (
        "You write a short, polite response letter for a small online seller who is answering a buyer's dispute.\n"
        f"Dispute type: {dispute_type}\n"
    )
    if question:
        p += f"Central question the platform will weigh: {question}\n"
    p += f"\nEVIDENCE (the only facts you may use):\n{ev}\n"
    if key_ids:
        p += f"\nKEY EVIDENCE for this dispute type (lead with these, right after stating the buyer's claim): {', '.join(key_ids)}\n"
    p += (
        "\nRULES:\n"
        "- Return the letter one factual sentence per line. Do not combine separate facts into one line.\n"
        "- First line: what the buyer claims. Second line: the key evidence that bears on that claim. Cite each line.\n"
        "- Then give the remaining supported facts in time order, each once.\n"
        "- Every factual line MUST end with one or more square-bracket citations, like [E2] or [E2][E3].\n"
        "- Use only numbers, dates, ids and names that appear in the cited evidence. Quote the evidence exactly.\n"
        "- If something is not in the evidence, do not write it. Never guess.\n"
        "- Place relevant evidence next to the claim, but draw no conclusions. Do not use words like clearly, proves, received or admitted unless the cited evidence uses them.\n"
        "- EVIDENCE IDs ARE INTERNAL: never write E1, E2, or any evidence id in prose. Evidence ids may appear only in final square-bracket citations such as [E9]. Never write 'E9 states', 'evidence E5', or similar.\n"
        + (PHOTO_RULES if has_photos else "") +
        "- Cite a name only with the evidence item that contains that name.\n"
        "- Do not describe a refund offer as a reason or an admission. State only what the message says.\n"
        "- Do not write any factual line without a citation. Do not write explanations or evidence summaries outside the letter.\n"
        "- Return only the letter; do not include reasoning, analysis, headings, or a list of evidence.\n"
        "- Start with 'Hello,' and end with 'Sincerely,'. Stay polite and factual.\n"
        "\nEXAMPLE OF THE STYLE (a different case with invented facts; never reuse its facts):\n" + EXAMPLE + "\n"
    )
    if issues:
        p += "\nYour previous draft had these problems. Fix them:\n" + "\n".join(f"- {i.line} -> {i.problem}" for i in issues)
        if previous:
            p += ("\n\nPREVIOUS DRAFT:\n" + previous + "\n\nReturn the full corrected letter. Change ONLY the flagged lines or clauses and keep every other line exactly as it is. "
                  + ("For a photo error, replace the visual claim with only 'A photo file named <file name> is on file [E#]' using the exact filename from the evidence. " if has_photos else "")
                  +
                  "For an evidence-id-in-prose or uncited-line error, rewrite that line as one factual sentence ending only with its final [E#] citation; never put an id in prose. "
                  "If you cannot support a clause with the evidence, delete it.")
    return p


class MockBackend:
    local = True
    """Deterministic offline drafter. Builds one cited sentence per evidence item.
    It is NOT a language model; it exists so the pipeline, verifier and tests run anywhere."""
    name = "mock"

    def generate(self, prompt, evidence, dispute_type, issues=None):
        out = ["Hello,"]
        for kind in ORDER:
            for e in (x for x in evidence if x.kind == kind):
                f, c = e.fields, f"[{e.id}]"
                if kind == "dispute_notice":
                    out.append(f'A case was opened on {f["opened"]} stating: "{f["reason"]}". {c}')
                elif kind == "order_record":
                    out.append(f'Order {f["order_id"]} for {f["item"]} (${f["amount"]:.2f}) was placed on {f["order_date"]}. {c}')
                elif kind == "shipping_tracking":
                    out.append(f'The order shipped on {f["ship_date"]} via {f["carrier"]} with tracking number {f["tracking"]}. {c}')
                elif kind == "delivery_confirmation":
                    out.append(f'The carrier shows the package delivered on {f["delivery_date"]}. {c}')
                elif kind in ("shipping_proof_photo", "item_photos"):
                    out.append(f'A photo file named {f["filename"]} is on file. {c}')
                elif kind == "listing_description":
                    out.append(f'The listing described the item as: "{f["text"]}" {c}')
                elif kind == "buyer_communication":
                    out.append(f'On {f["ts"].split()[0]}, the {f["sender"]} wrote: "{f["text"]}" {c}')
                elif kind == "policy_reference":
                    out.append(f'The seller relies on this policy excerpt: "{f["text"]}" {c}')
        out += ["I ask that this case be resolved in the seller's favor based on the evidence above.",
                "Thank you for reviewing this case.", "Sincerely,"]
        return "\n".join(out)


class OllamaBackend:
    """Calls a local Ollama server. Model choice: --model, else DD_MODEL, else the first installed Gemma."""
    name = "ollama"

    def __init__(self, url=None, model=None, timeout=None):
        self.url = (url or os.environ.get("DD_OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.model = model or os.environ.get("DD_MODEL") or None
        self.timeout = float(timeout or os.environ.get("DD_TIMEOUT", 300))
        self.num_predict = int(os.environ.get("DD_NUM_PREDICT", 512))
        self.max_seconds = float(os.environ.get("DD_MAX_SECONDS", 1200))
        self.last_stats = None
        self._checked = False

    @property
    def local(self):
        """True when the model server is on this machine, so customer data does not leave it."""
        return (urllib.parse.urlparse(self.url).hostname or "") in ("localhost", "127.0.0.1", "::1")

    def _installed(self):
        try:
            with urllib.request.urlopen(f"{self.url}/api/tags", timeout=10) as r:
                return [m["name"] for m in json.loads(r.read()).get("models", [])]
        except Exception:
            return None  # older server or unreachable; the generate call will report the real problem

    def _resolve_model(self):
        if self._checked:
            return
        installed = self._installed()
        if installed is not None:
            if self.model is None:
                gemma = [n for n in installed if n.lower().startswith("gemma")]
                pick = (gemma or installed or [None])[0]
                if pick is None:
                    raise BackendError("Ollama is running but has no models. Run: ollama pull gemma3:4b")
                self.model = pick
            elif not (self.model in installed or (":" not in self.model and f"{self.model}:latest" in installed)):
                raise BackendError(f"Model '{self.model}' is not installed in Ollama. Installed: {', '.join(installed) or 'none'}. "
                                   f"Use --model <name> or set DD_MODEL, or run: ollama pull {self.model}")
        elif self.model is None:
            self.model = "gemma3"
        self._checked = True

    def _stall_message(self, first, tokens):
        if first is None:
            return (f"No first word from Ollama within {self.timeout:.0f} s (model {self.model}). The model may still be loading, "
                    "or the computer is overloaded or went to sleep. Check `ollama ps`, close other programs, plug in the laptop, "
                    "or raise --timeout.")
        return f"Ollama stalled: no new text for {self.timeout:.0f} s after {tokens} tokens (model {self.model}). The computer may be overloaded or asleep."

    def generate(self, prompt, evidence, dtype, issues=None):
        """Streams the answer so a slow but working model is not mistaken for a stuck one: --timeout is the longest the
        server may stay silent, DD_MAX_SECONDS caps the whole call. Records speed in self.last_stats."""
        self._resolve_model()
        body = json.dumps({"model": self.model, "prompt": prompt, "stream": True,
                           "options": {"temperature": 0.3, "num_predict": self.num_predict}}).encode()
        req = urllib.request.Request(f"{self.url}/api/generate", data=body, headers={"Content-Type": "application/json"})
        t0, first, parts, tokens, reason, eval_count = time.perf_counter(), None, [], 0, None, None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                for raw in r:
                    if time.perf_counter() - t0 > self.max_seconds:
                        raise BackendError(f"Ollama took longer than {self.max_seconds:.0f} s in total (model {self.model}); stopped. "
                                           "Raise DD_MAX_SECONDS or use a smaller model.")
                    line = raw.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if obj.get("error"):
                        raise BackendError(f"Ollama error (model {self.model}): {obj['error']}")
                    frag = obj.get("response", "")
                    if frag:
                        if first is None:
                            first = time.perf_counter() - t0
                        parts.append(frag)
                        tokens += 1
                    if obj.get("done"):
                        reason, eval_count = obj.get("done_reason"), obj.get("eval_count")
                        break
        except BackendError:
            raise
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise BackendError(f"Ollama says model '{self.model}' was not found (HTTP 404). Run `ollama list` and pass the exact name with --model.") from e
            raise BackendError(f"Ollama call failed at {self.url} (model {self.model}): {e}") from e
        except (TimeoutError, socket.timeout) as e:
            raise BackendError(self._stall_message(first, tokens)) from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, (TimeoutError, socket.timeout)):
                raise BackendError(self._stall_message(first, tokens)) from e
            raise BackendError(f"Ollama call failed at {self.url} (model {self.model}): {e}") from e
        except Exception as e:
            raise BackendError(f"Ollama call failed at {self.url} (model {self.model}): {e}") from e
        total = time.perf_counter() - t0
        n = int(eval_count or tokens)
        gen = total - (first or 0)
        self.last_stats = {"tokens": n, "seconds": round(total, 1), "first_token_seconds": round(first, 1) if first is not None else None,
                           "tokens_per_second": round(n / gen, 1) if gen > 0 and n else None,
                           "done_reason": reason, "truncated": reason == "length"}
        text = "".join(parts).strip()
        if not text:
            raise BackendError(f"Ollama returned an empty answer (model {self.model}).")
        return text


def get_backend(name, model=None, timeout=None):
    if name == "mock":
        return MockBackend()
    if name == "ollama":
        return OllamaBackend(model=model, timeout=timeout)
    raise ValueError(f"unknown backend '{name}' (use mock or ollama)")
