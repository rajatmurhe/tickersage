"""Finds secrets pasted into a question and redacts them before anything else sees the text.

It runs first in the graph's input step, so the LLM, logs, traces, caches and the audit log
only ever receive the redacted question. It detects:

  * PEM private keys (PKCS#8, RSA, EC, DSA, OpenSSH, encrypted, PGP), including a block pasted
    without its END line
  * raw 64-hex private keys, with or without 0x
  * Binance-style 64-character API keys and secrets
  * BIP39 seed phrases: 12 or more consecutive words from the 2048-word English list (so 12, 15,
    18, 21 and 24-word phrases), allowing one mistyped word and numbering such as "1. word 2) word"

A transaction hash has the same shape as a hex private key, so it is redacted too. That false
positive costs nothing here (there is no transaction tool); a miss could cost someone their funds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

WORDLIST = Path(__file__).parent / "data" / "bip39_english.txt"
BIP39_WORDS = frozenset(WORDLIST.read_text().split())
SEED_MIN_WORDS = 12
_EDGE = 3  # a run of fewer BIP39 words than this next to a full phrase is a label, not part of it

PLACEHOLDERS = {
    "pem_private_key": "[REDACTED_PEM_PRIVATE_KEY]",
    "private_key": "[REDACTED_PRIVATE_KEY]",
    "api_key": "[REDACTED_API_KEY]",
    "seed_phrase": "[REDACTED_SEED_PHRASE]",
}
KIND_LABELS = {
    "pem_private_key": "a PEM private key",
    "private_key": "a private key",
    "api_key": "an exchange API key or secret",
    "seed_phrase": "a wallet seed phrase",
}

_PEM_BEGIN = r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
_PEM_END = r"-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
_PEM_BLOCK = re.compile(rf"{_PEM_BEGIN}.*?{_PEM_END}", re.S)
# A block with no END line: take the header lines and base64 lines that follow the BEGIN line, plus a short
# last line when it stands alone on its line.
_PEM_OPEN = re.compile(
    rf"{_PEM_BEGIN}(?:\s+(?:(?:Proc-Type|DEK-Info|Comment|Version):[^\n]*|[A-Za-z0-9+/]{{16,}}={{0,2}}))*"
    r"(?:\s+[A-Za-z0-9+/]{2,}={1,2}|[ \t]*\r?\n[A-Za-z0-9+/]{1,15}={0,2}[ \t]*(?=\r?\n|$))?"
)
_HEX_KEY = re.compile(r"(?<![0-9A-Za-z])(?:0[xX])?[0-9a-fA-F]{64}(?![0-9A-Za-z])")
_API_KEY = re.compile(
    r"(?<![0-9A-Za-z])(?=[0-9A-Za-z]{0,63}[0-9])(?=[0-9A-Za-z]{0,63}[A-Za-z])[0-9A-Za-z]{64}(?![0-9A-Za-z])"
)
_WORD = re.compile(r"[A-Za-z]+")
_PLACEHOLDER = re.compile(r"\[REDACTED_[A-Z_]+\]")
# What may sit between two words of one seed phrase: spaces, punctuation and numbering ("1." "2)" "#3").
_SEED_GAP = re.compile(r"[\s,;.:|/()\[\]#'\"*0-9-]*")


@dataclass
class Redaction:
    text: str
    kinds: list[str] = field(default_factory=list)  # one entry per kind found, in order of first appearance
    count: int = 0  # secrets removed

    @property
    def found(self) -> bool:
        return self.count > 0


def redact_secrets(text: str) -> Redaction:
    out = Redaction(text)
    for kind, pattern in (("pem_private_key", _PEM_BLOCK), ("pem_private_key", _PEM_OPEN),
                          ("private_key", _HEX_KEY), ("api_key", _API_KEY)):
        out.text, n = pattern.subn(PLACEHOLDERS[kind], out.text)
        _note(out, kind, n)
    spans = _seed_spans(out.text)
    for start, end in reversed(spans):
        out.text = out.text[:start] + PLACEHOLDERS["seed_phrase"] + out.text[end:]
    _note(out, "seed_phrase", len(spans))
    return out


def _note(out: Redaction, kind: str, n: int) -> None:
    if n:
        out.count += n
        if kind not in out.kinds:
            out.kinds.append(kind)


def _seed_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of seed-phrase-like word runs: >= 12 words, all BIP39 but at most one."""
    spans: list[tuple[int, int]] = []
    segment: list[re.Match[str]] = []
    prev_end = None
    # Earlier placeholders end a run: "KEY" in [REDACTED_API_KEY] is a BIP39 word, but not part of a phrase.
    placeholders = [p.span() for p in _PLACEHOLDER.finditer(text)]
    for m in _WORD.finditer(text):
        in_placeholder = any(a <= m.start() < b for a, b in placeholders)
        if in_placeholder or (prev_end is not None and not _SEED_GAP.fullmatch(text, prev_end, m.start())):
            spans += _runs(segment)
            segment = []
        if in_placeholder:
            prev_end = None
            continue
        segment.append(m)
        prev_end = m.end()
    spans += _runs(segment)
    return _merge(spans)


def _runs(words: list[re.Match[str]]) -> list[tuple[int, int]]:
    """Spans of `words` that look like a seed phrase: a run of >= 12 BIP39 words, or two runs joined by one
    other word (a typo) that reach 12 together.

    Two runs are not joined when one of them is a phrase on its own and the other is only a word or two:
    that is more likely a label ("price please, words: abandon ability ...") than a typo.
    """
    known = [w.group(0).lower() in BIP39_WORDS for w in words]
    runs: list[tuple[int, int]] = []  # (first, last) index of each maximal run of BIP39 words
    i = 0
    while i < len(known):
        if not known[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(known) and known[j + 1]:
            j += 1
        runs.append((i, j))
        i = j + 1
    found = []
    for k, (a, b) in enumerate(runs):
        if b - a + 1 >= SEED_MIN_WORDS:
            found.append((a, b))
        if k + 1 == len(runs) or runs[k + 1][0] - b != 2:
            continue  # last run, or more than one other word before the next run
        c, d = runs[k + 1]
        left, right = b - a + 1, d - c + 1
        if (left < _EDGE and right >= SEED_MIN_WORDS) or (right < _EDGE and left >= SEED_MIN_WORDS):
            continue
        if left + 1 + right >= SEED_MIN_WORDS:
            found.append((a, d))
    return [(words[a].start(), words[b].end()) for a, b in found]


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def secret_warning(kinds: list[str]) -> str:
    """The note shown at the top of the answer when something was redacted."""
    labels = [KIND_LABELS[k] for k in kinds]
    what = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]
    steps = []
    if "api_key" in kinds:
        steps.append("Delete that API key in your exchange's API management page and create a new one.")
    if "seed_phrase" in kinds or "private_key" in kinds:
        steps.append("If it controls a wallet, move the funds to a new wallet with a new seed phrase.")
    if "pem_private_key" in kinds:
        steps.append("Revoke that key and generate a new one.")
    if kinds == ["private_key"]:
        steps.append("If it was a transaction hash, you can ignore this.")
    return (
        f"**Security warning:** your message looked like it contained {what}. I removed it before doing "
        "anything else, so it was not sent to the language model or kept in logs, traces, caches or the audit "
        "log. Treat it as exposed anyway. " + " ".join(steps) + " Never paste keys or seed phrases into a chat."
    )
