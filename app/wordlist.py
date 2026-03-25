from __future__ import annotations

from typing import Iterable, List


def parse_wordlist(raw_text: str) -> List[str]:
    """Parse and normalize a wordlist while preserving first-seen order."""
    seen = set()
    words: List[str] = []

    for line in raw_text.splitlines():
        item = line.strip().lower()
        if not item or item.startswith("#"):
            continue
        if item in seen:
            continue
        seen.add(item)
        words.append(item)

    return words


def parse_wordlist_bytes(content: bytes, encoding_candidates: Iterable[str] = ("utf-8", "latin-1")) -> List[str]:
    for encoding in encoding_candidates:
        try:
            text = content.decode(encoding)
            return parse_wordlist(text)
        except UnicodeDecodeError:
            continue
    text = content.decode("utf-8", errors="ignore")
    return parse_wordlist(text)
