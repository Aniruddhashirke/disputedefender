"""Fail-closed verifier. Every line of the letter must cite evidence, and what the line says must be
supported by the evidence it cites: quotes, numbers, dates, ids, names and strong-claim words.
Anything else is flagged. This is a lexical check; it cannot judge meaning (see README, known limits)."""
import difflib
import re

from .models import Issue

CITE = re.compile(r"\[E(\d+)\]")
NUM = re.compile(r"\d[\d.,:/-]*")
QUOTE = re.compile(r'"([^"]{3,})"')
ALNUM = re.compile(r"[A-Za-z0-9-]{5,}")
MONTH = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
MONTH_NUM = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
# Words that assert more than a record usually shows. They must appear in the cited evidence itself.
STRONG = re.compile(r"\b(clearly|obviously|undeniably|definitely|proves?|proof|proven|admitted|admits|acknowledged|"
                    r"received|signed|guaranteed|fraud|fraudulent|lying|lied|always|never|"
                    r"sorry|apolog\w*|regrets?|regretted|indeed|our fault|our mistake)\b", re.I)
# Whole-line boilerplate only. A line that starts politely but goes on to claim something is NOT boilerplate.
BOILERPLATE = [
    re.compile(r"^(dear [\w .'-]{1,40}|hello|hi)[,!]?$", re.I),
    re.compile(r"^thank you( for (your time|your attention|reviewing this case|considering this))?[.!]?$", re.I),
    re.compile(r"^(sincerely|regards|best regards|kind regards)[,.]?$", re.I),
    re.compile(r"^i ask that this case be resolved in the seller's favor( based on the evidence( above| provided)?)?\.?$", re.I),
]
GROUP = re.compile(r"((?:\s*\[E\d+\])+)")
PHOTO_KINDS = {"shipping_proof_photo", "item_photos"}
# Nothing about a photo may be claimed beyond the fact that a file with that name exists.
PHOTO_CLAIM = re.compile(r"\b(shows?|showing|shown|depict\w*|taken|captur\w*|packed|packaged|pictur\w*|image of|photo of|photograph of)\b", re.I)
# Capitalised words that are not claims about the case.
ALLOWED_CAPS = {"etsy", "ebay", "shopify",
                # ordinary words that models capitalise after a dash or colon; they are not names or claims
                "order", "item", "package", "parcel", "tracking", "delivery", "shipment", "shipping", "case", "photo",
                "listing", "buyer", "seller", "carrier", "customer", "we", "our", "the", "this", "it", "they", "a", "an"}
# Emphasis words that can be removed from a clause without changing any fact.
ADVERBS = {"clearly", "obviously", "undeniably", "definitely", "indeed"}
# [E1, E2], [E1;E2], [E1 and E2], (E4), [e4], [ E4 ] all mean the same thing as [E1][E2] / [E4].
CITE_VARIANT = re.compile(r"[\[(]\s*(E\d+(?:\s*(?:,|;|/|&|and)\s*E\d+)*)\s*[\])]", re.I)
_QMAP = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'", "\u00a0": " "})


def _norm(text):
    """Models often swap straight quotes for curly ones. Normalise so quote checks cannot be bypassed."""
    return text.translate(_QMAP)


def _expand_cites(m):
    return "".join(f"[E{n}]" for n in re.findall(r"(?i)e\s*(\d+)", m.group(1)))


def canonicalize_line(line):
    """Undo harmless formatting habits of language models: bold markers, bullets, numbering, header marks, and
    citation variants such as [E1, E2] or (E4). Lossless: it changes how a line is written, never what it says."""
    line = re.sub(r"\*\*|__|`", "", line)
    line = re.sub(r"^\s*(?:[-*\u2022]+|\d{1,2}[.)]|#{1,6})\s+", "", line)
    return CITE_VARIANT.sub(_expand_cites, line)


def canonicalize(text):
    return "\n".join(canonicalize_line(_norm(l)) for l in text.splitlines())


def _clean_q(s):
    return re.sub(r"\s+", " ", s).strip().strip(".,;:!?\u2026 ").lower()


def _quote_ok(q, support):
    """A quote must appear verbatim in the evidence. Punctuation at its ends and '...' gaps are ignored, because
    writers put the full stop inside the quotation marks even when the source does not."""
    sup = re.sub(r"\s+", " ", support)
    parts = [_clean_q(p) for p in re.split(r"\.\.\.|\u2026", q)]
    parts = [p for p in parts if len(p) >= 3]
    return all(p in sup for p in parts)


def _best_window(q, texts, threshold=0.9):
    """The stretch of evidence text most similar to q, if it is a near match (a typo-level difference)."""
    target = _clean_q(q)
    n = len(q.split())
    best, best_text = 0.0, None
    for t in texts:
        words = t.split()
        for size in range(max(1, n - 2), n + 3):
            for i in range(len(words) - size + 1):
                window = " ".join(words[i:i + size])
                ratio = difflib.SequenceMatcher(None, target, _clean_q(window)).ratio()
                if ratio > best:
                    best, best_text = ratio, window
    return best_text.strip(".,;:!?\u2026 ") if best >= threshold and best_text else None


def _fix_quote(body, cited):
    texts = [_norm(e.text) for e in cited]
    support = " ".join(texts).lower()

    def repl(m):
        if _quote_ok(m.group(1), support):
            return m.group(0)
        near = _best_window(m.group(1), texts)
        return f'"{near}"' if near else m.group(0)
    return QUOTE.sub(repl, body)


def _remove_word(body, word):
    body = re.sub(r"\b" + re.escape(word) + r"\b,?\s*", "", body, flags=re.I)
    return re.sub(r"\s{2,}", " ", body).strip()


def _in_support(tok, support):
    """Whole-token match, so '4' does not pass just because '2026-03-04' contains a 4."""
    return re.search(r"(?<![0-9A-Za-z.-])" + re.escape(tok) + r"(?![0-9A-Za-z])", support) is not None


def _names_not_supported(body, support):
    """Capitalised words in the middle of a sentence (names, carriers, places) must appear in the cited evidence."""
    words = QUOTE.sub(" ", body).split()
    for i, w in enumerate(words):
        if i == 0 or words[i - 1].endswith((".", "!", "?", ":")) or any(c.isdigit() for c in w):
            continue  # ids and numbers are checked separately
        tok = re.sub(r"^[^A-Za-z]+|[^A-Za-z'-]+$", "", w)
        if len(tok) >= 2 and tok[0].isupper() and tok.lower() not in support and tok.lower() not in ALLOWED_CAPS:
            return tok
    return None


def _check_line(body, support, cited_ev=()):
    if re.search(r"\bE\d+\b", body):
        return "evidence ids must appear only as [E#] citations at the end of a clause, not written in the sentence"
    photos = [e for e in cited_ev if e.kind in PHOTO_KINDS]
    if photos:
        names = [e.fields.get("filename", "") for e in photos]
        if not any(n and n in body for n in names):
            return "photo evidence must be referred to by its file name"
        rest = body
        for n in names:
            rest = rest.replace(n, " ")
        if PHOTO_CLAIM.search(rest):
            return "photo described beyond its file name (what it shows or when it was taken is not in the evidence)"
    for q in QUOTE.findall(body):
        if not _quote_ok(q, support):
            return f'quoted text not found in cited evidence: "{q[:40]}"'
    plain = QUOTE.sub(" ", body)
    for m in STRONG.finditer(plain):
        w = m.group(1).lower()
        if not re.search(r"\b" + re.escape(w) + r"\b", support):
            return f"strong claim word not in cited evidence: {w}"
    for m in MONTH.finditer(body):
        literal = " ".join(m.group(0).lower().replace(".", "").split())
        iso = f"-{MONTH_NUM[m.group(1)[:3].lower()]:02d}-{int(m.group(2)):02d}"
        if literal not in support and iso not in support:
            return f"date not in cited evidence: {m.group(0)}"
    body = MONTH.sub(" ", body)
    bad = _names_not_supported(body, support)
    if bad:
        return f"name or proper noun not in cited evidence: {bad}"
    for t in ALNUM.findall(body):
        if re.search(r"\d", t) and re.search(r"[A-Za-z]", t):
            if t.lower() not in support:
                return f"id not in cited evidence: {t}"
    body = ALNUM.sub(lambda m: " " if re.search(r"\d", m.group(0)) and re.search(r"[A-Za-z]", m.group(0)) else m.group(0), body)
    for tok in NUM.findall(body):
        tok = tok.rstrip(".,:/-")
        if tok and not _in_support(tok.lower(), support):
            return f"number or date not in cited evidence: {tok}"
    return None


FIXABLE = ("number or date not in", "date not in", "id not in", "name or proper noun not in")


def _fix_clause(body, ids, by_id):
    """Repair a clause without losing or inventing anything. Handles: emphasis words that are not in the evidence
    (removed), a quote that is a near miss of the evidence (replaced by the exact text), and a true fact cited to the
    wrong evidence item (the one evidence item that contains it is added). Returns (body, ids, notes) or None."""
    ids, notes = list(ids), []
    for _ in range(6):
        cited = [by_id[f"E{c}"] for c in ids]
        support = _norm(" ".join(e.text for e in cited)).lower()
        problem = _check_line(body, support, cited)
        if problem is None:
            return body, ids, notes
        if problem.startswith("strong claim word") and ": " in problem:
            word = problem.rsplit(": ", 1)[1].strip().lower()
            if word not in ADVERBS:
                return None
            body = _remove_word(body, word)
            notes.append(f"removed the word '{word}' because the evidence does not say it")
            continue
        if problem.startswith("quoted text not found"):
            fixed = _fix_quote(body, cited)
            if fixed == body:
                return None
            body = fixed
            notes.append("corrected a near-match quote to the exact evidence text")
            continue
        if problem.startswith(FIXABLE) and ": " in problem:
            token = problem.rsplit(": ", 1)[1].strip().lower()
            pattern = re.compile(r"(?<![0-9A-Za-z.-])" + re.escape(token) + r"(?![0-9A-Za-z])")
            hits = [e for e in by_id.values() if e.id[1:] not in ids and e.kind not in PHOTO_KINDS and pattern.search(_norm(e.text).lower())]
            if len(hits) != 1:  # not found anywhere, or ambiguous: do not guess
                return None
            ids.append(hits[0].id[1:])
            notes.append(f"completed a citation: added {hits[0].id} (the fact is in that evidence, not the one cited)")
            continue
        return None
    return None


def _lead(text):
    return re.sub(r"^[\s.,;:!?\u2013\u2014-]+", "", text).strip()


def check_clauses(line, by_id):
    """Split a cited line into clauses (text ending in a citation group) and check each against only its own evidence.
    Returns ([(text, group, ids, problem_or_None)], trailing_text)."""
    parts = GROUP.split(line)
    clauses = []
    for i in range(0, len(parts) - 1, 2):
        text, grp = parts[i], parts[i + 1]
        ids = CITE.findall(grp)
        missing = [c for c in ids if f"E{c}" not in by_id]
        problem = None
        if missing:
            problem = f"cites unknown evidence: {', '.join('E' + c for c in missing)}"
        elif _lead(text):
            cited_ev = [by_id[f"E{c}"] for c in ids]
            support = _norm(" ".join(e.text for e in cited_ev)).lower()
            problem = _check_line(_lead(text), support, cited_ev)
        clauses.append((text, grp, ids, problem))
    trailing = parts[-1] if len(parts) % 2 == 1 else ""
    return clauses, trailing


def verify_letter(letter, evidence, require_facts=False):
    """Line-level and clause-level: each stretch of text ending in a citation is checked against ONLY the
    evidence cited right there, so one citation cannot vouch for a claim made next to a different one."""
    by_id = {e.id: e for e in evidence}
    issues, ok_lines = [], []
    for raw in letter.splitlines():
        line = canonicalize_line(_norm(raw)).strip()
        if not line:
            continue
        if not CITE.search(line):
            if any(p.match(line) for p in BOILERPLATE):
                ok_lines.append(line)
            else:
                issues.append(Issue(line, "no evidence citation"))
            continue
        clauses, trailing = check_clauses(line, by_id)
        problem = next((c[3] for c in clauses if c[3]), None)
        if not problem and re.search(r"[A-Za-z0-9]", trailing):
            problem = "text after the last citation has no evidence"
        if problem:
            issues.append(Issue(line, problem))
        else:
            ok_lines.append(line)
    if require_facts and not any(CITE.search(l) for l in ok_lines):
        issues.append(Issue("(whole letter)", "letter contains no cited facts"))
    return issues, ok_lines


CONJ = re.compile(r"^(and|but|which|while|so|because|as)\b", re.I)


def _cap(t):
    return t[:1].upper() + t[1:]


def _emit(out, broke, text, body, grp):
    """Append a kept or repaired clause to the line being rebuilt."""
    grp = " " + grp.strip()
    if not out:
        return _cap(body) + grp
    if broke:
        return out.rstrip(" ,;.") + ". " + _cap(body) + grp
    return out + re.match(r"^[\s.,;:!?\u2013\u2014-]*", text).group(0) + body + grp


def repair_letter(letter, evidence):
    """Deterministic last resort. Keeps every clause that verifies, fixes what can be fixed without losing or inventing
    anything (see _fix_clause), rewrites a bad photo clause to the safe template (file name only), and drops the
    rest. Never adds a claim. Returns (letter, notes); notes starting with 'dropped' mean content was lost."""
    by_id = {e.id: e for e in evidence}
    out_lines, notes = [], []
    for raw in letter.splitlines():
        line = canonicalize_line(_norm(raw)).strip()
        if not line:
            continue
        if not CITE.search(line):
            if any(p.match(line) for p in BOILERPLATE):
                out_lines.append(line)
            else:
                notes.append(f"dropped uncited line: {line[:70]}")
            continue
        clauses, trailing = check_clauses(line, by_id)
        out, broke = "", False
        for text, grp, ids, problem in clauses:
            body = _lead(text)
            if not body:
                continue
            if problem is None:
                if broke and CONJ.match(body):
                    notes.append(f"dropped a clause that could not stand alone: {body[:60]}")
                    continue
                out, broke = _emit(out, broke, text, body, grp), False
                continue
            fixed = None if "unknown evidence" in problem else _fix_clause(body, ids, by_id)
            if fixed is not None:
                new_body, new_ids, fix_notes = fixed
                if broke and CONJ.match(new_body):
                    notes.append(f"dropped a clause that could not stand alone: {new_body[:60]}")
                    continue
                notes += fix_notes
                out, broke = _emit(out, broke, text, new_body, "".join(f"[E{c}]" for c in new_ids)), False
                continue
            photos = [by_id[f"E{c}"] for c in ids if f"E{c}" in by_id and by_id[f"E{c}"].kind in PHOTO_KINDS]
            if photos and "photo" in problem:
                sent = f"A photo file named {photos[0].fields.get('filename', '')} is on file [E{photos[0].id[1:]}]"
                out = (out.rstrip(" ,;.") + ". " + sent) if out else sent
                notes.append("rewrote a photo clause so it names only the file")
            else:
                notes.append(f"dropped ({problem}): {body[:60]}")
            broke = True
        if re.search(r"[A-Za-z0-9]", trailing):
            notes.append(f"dropped uncited text: {trailing.strip()[:70]}")
        if out:
            out_lines.append(out.rstrip(" ,;") + ("" if out.rstrip().endswith(".") else "."))
    return "\n".join(out_lines), notes
