#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = ROOT_DIR / "output"
DEFAULT_PREFIX = "domain-value"
DEFAULT_REFERENCE_WORDLIST = ROOT_DIR / "Wordlists" / "google-10000-english-master" / "google-10000-english-usa-no-swears.txt"

DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)

VOWELS = set("aeiou")
TOKEN_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")

COMMERCIAL_KEYWORDS: Dict[str, int] = {
    "agent": 10,
    "analytics": 8,
    "ai": 8,
    "bio": 8,
    "capital": 8,
    "cloud": 9,
    "compute": 8,
    "data": 9,
    "energy": 8,
    "finance": 8,
    "health": 9,
    "index": 5,
    "labs": 7,
    "lab": 6,
    "med": 7,
    "pay": 7,
    "quantum": 10,
    "quant": 8,
    "robot": 7,
    "security": 10,
    "secure": 8,
    "solar": 8,
    "tech": 7,
}

TREND_KEYWORDS: Dict[str, int] = {
    "agent": 16,
    "analytics": 11,
    "ai": 18,
    "automation": 13,
    "bio": 14,
    "chain": 8,
    "cloud": 12,
    "climate": 12,
    "compute": 12,
    "crypto": 10,
    "data": 12,
    "energy": 11,
    "health": 13,
    "med": 11,
    "quantum": 16,
    "quant": 12,
    "robot": 12,
    "security": 13,
    "secure": 11,
    "solar": 11,
}

LOW_LIQUIDITY_SUFFIXES: Tuple[str, ...] = (
    "ability",
    "ation",
    "ement",
    "ibility",
    "ically",
    "iness",
    "ingly",
    "ions",
    "ities",
    "less",
    "lessly",
    "ment",
    "ness",
    "ology",
    "ship",
    "sion",
    "tion",
    "tions",
    "ments",
)

# Conservative built-in blocklist for obvious trademark conflicts.
# Users can extend this with --trademark-blocklist.
DEFAULT_TRADEMARK_TERMS: Tuple[str, ...] = (
    "adidas",
    "airbnb",
    "airbus",
    "airpods",
    "alexa",
    "alibaba",
    "alphabet",
    "amazon",
    "android",
    "apple",
    "atlassian",
    "audi",
    "autodesk",
    "baidu",
    "bmw",
    "canva",
    "chatgpt",
    "cisco",
    "cocacola",
    "costco",
    "dell",
    "disney",
    "dropbox",
    "ebay",
    "espn",
    "facebook",
    "fedex",
    "figma",
    "ford",
    "github",
    "gmail",
    "google",
    "gucci",
    "honda",
    "huawei",
    "hyundai",
    "ibm",
    "instagram",
    "intel",
    "ipad",
    "iphone",
    "jaguar",
    "kfc",
    "kia",
    "kindle",
    "lamborghini",
    "lenovo",
    "linkedin",
    "louisvuitton",
    "lyft",
    "macbook",
    "mastercard",
    "mcdonalds",
    "mercedes",
    "meta",
    "microsoft",
    "netflix",
    "nike",
    "nintendo",
    "nvidia",
    "openai",
    "oracle",
    "paypal",
    "pepsi",
    "pinterest",
    "porsche",
    "prada",
    "qualcomm",
    "reddit",
    "reebok",
    "rolex",
    "samsung",
    "salesforce",
    "shopify",
    "snapchat",
    "sony",
    "spacex",
    "spotify",
    "starbucks",
    "stripe",
    "tesla",
    "threads",
    "tiktok",
    "toyota",
    "uber",
    "visa",
    "volkswagen",
    "whatsapp",
    "windows",
    "xbox",
    "xiaomi",
    "youtube",
    "zara",
    "zoom",
)

DISALLOWED_TERMS: Tuple[str, ...] = (
    "hentai",
    "porn",
    "porno",
    "sex",
    "xxx",
    "nude",
    "naked",
    "fetish",
    "casino",
    "gamble",
    "betting",
    "weapon",
    "drugs",
)


@dataclass
class FileScanStats:
    path: str
    total_lines: int = 0
    blank_lines: int = 0
    invalid_lines: int = 0
    valid_lines: int = 0
    duplicate_valid_lines: int = 0

    def to_dict(self) -> Dict[str, object]:
        return {
            "path": self.path,
            "total_lines": self.total_lines,
            "blank_lines": self.blank_lines,
            "invalid_lines": self.invalid_lines,
            "valid_lines": self.valid_lines,
            "duplicate_valid_lines": self.duplicate_valid_lines,
        }


@dataclass
class DomainRecord:
    domain: str
    occurrences: int = 0
    source_files: Set[str] = field(default_factory=set)
    first_seen_file: Optional[str] = None
    first_seen_line: Optional[int] = None

    @property
    def source_file_count(self) -> int:
        return len(self.source_files)

    @property
    def first_seen(self) -> str:
        if not self.first_seen_file or self.first_seen_line is None:
            return ""
        return f"{self.first_seen_file}:{self.first_seen_line}"


@dataclass
class DomainScore:
    domain: str
    sld: str
    tld: str
    sld_length: int
    resale_score: float
    future_score: float
    score: float
    tier: str
    keyword_hits: List[str]
    trademark_hits: List[str]
    trademark_risk: str
    strengths: List[str]
    risks: List[str]
    source_files: List[str]
    source_file_count: int
    occurrences: int
    first_seen: str

    def to_csv_row(self, rank: int) -> Dict[str, object]:
        return {
            "rank": rank,
            "domain": self.domain,
            "score": f"{self.score:.2f}",
            "resale_score": f"{self.resale_score:.2f}",
            "future_score": f"{self.future_score:.2f}",
            "tier": self.tier,
            "sld_length": self.sld_length,
            "keyword_hits": ";".join(self.keyword_hits),
            "trademark_hits": ";".join(self.trademark_hits),
            "trademark_risk": self.trademark_risk,
            "strengths": ";".join(self.strengths),
            "risks": ";".join(self.risks),
            "source_file_count": self.source_file_count,
            "occurrences": self.occurrences,
            "first_seen": self.first_seen,
            "source_files": ";".join(self.source_files),
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministically scan output domain lists, score resale/upside potential, and export ranked picks."
    )
    parser.add_argument(
        "--input-dir",
        default=str(DEFAULT_INPUT_DIR),
        help=f"Directory with generated domain text files (default: {DEFAULT_INPUT_DIR}).",
    )
    parser.add_argument(
        "--glob",
        default="*.txt",
        help="File pattern inside input dir (default: *.txt).",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively match files under input dir.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for ranked exports (default: same as input-dir).",
    )
    parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help=f"Filename prefix for exports (default: {DEFAULT_PREFIX}).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=300,
        help="Max number of top candidates to export (default: 300).",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=55.0,
        help="Minimum combined score for shortlist inclusion (default: 55.0).",
    )
    parser.add_argument(
        "--print-top",
        type=int,
        default=25,
        help="How many top rows to print to terminal (default: 25).",
    )
    parser.add_argument(
        "--disable-trademark-filter",
        action="store_true",
        help="Do not exclude trademark-risk domains from shortlist (default: filter enabled).",
    )
    parser.add_argument(
        "--allow-medium-trademark-risk",
        action="store_true",
        help="When trademark filtering is enabled, allow medium-risk domains into shortlist.",
    )
    parser.add_argument(
        "--trademark-blocklist",
        default=None,
        help="Optional newline-delimited file with additional trademark terms to block.",
    )
    return parser


def _split_domain(domain: str) -> Tuple[str, str]:
    sld, tld = str(domain).strip().lower().rsplit(".", 1)
    return sld, tld


def _max_repeated_char_run(text: str) -> int:
    if not text:
        return 0
    run = 1
    best = 1
    previous = text[0]
    for char in text[1:]:
        if char == previous:
            run += 1
            if run > best:
                best = run
        else:
            previous = char
            run = 1
    return best


def _max_consonant_run(text: str) -> int:
    run = 0
    best = 0
    for char in text:
        if not char.isalpha() or char in VOWELS:
            run = 0
            continue
        run += 1
        if run > best:
            best = run
    return best


def _keyword_match(sld: str, keyword: str) -> bool:
    if keyword == "ai":
        # Avoid treating every word starting with "ai..." as intentional AI branding.
        return sld.endswith("ai") or (sld.startswith("ai") and len(sld) <= 6)
    if len(keyword) <= 3:
        return sld.startswith(keyword) or sld.endswith(keyword)
    return keyword in sld


def _keyword_hits(sld: str, keywords: Dict[str, int]) -> List[str]:
    hits = []
    for keyword in sorted(keywords.keys(), key=lambda item: (-len(item), item)):
        if _keyword_match(sld, keyword):
            hits.append(keyword)
    return hits


def _length_points(length: int) -> int:
    if length <= 3:
        return 6
    if length <= 4:
        return 14
    if length <= 6:
        return 28
    if length <= 8:
        return 34
    if length <= 10:
        return 30
    if length <= 12:
        return 22
    if length <= 14:
        return 12
    if length <= 16:
        return 4
    return -8


def _brevity_points(length: int) -> int:
    if length <= 6:
        return 22
    if length <= 8:
        return 20
    if length <= 10:
        return 15
    if length <= 12:
        return 10
    if length <= 14:
        return 5
    return 0


def _clamp(score: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, score))


def _normalize_token(value: str) -> str:
    return TOKEN_NORMALIZE_RE.sub("", str(value or "").strip().lower())


def load_trademark_terms(path: Path) -> Set[str]:
    terms: Set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        normalized = _normalize_token(line)
        if len(normalized) >= 3:
            terms.add(normalized)
    return terms


def load_reference_words(path: Path) -> Set[str]:
    words: Set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        word = _normalize_token(raw_line)
        if len(word) >= 3:
            words.add(word)
    return words


def _trademark_hits(sld: str, trademark_terms: Set[str]) -> List[str]:
    sld_norm = _normalize_token(sld)
    hits: List[str] = []
    for term in sorted(trademark_terms, key=lambda item: (-len(item), item)):
        if not term:
            continue
        if len(term) <= 4:
            if sld_norm == term or sld_norm.startswith(term) or sld_norm.endswith(term):
                hits.append(term)
            continue
        if term in sld_norm:
            hits.append(term)
    return hits


def _trademark_risk_level(sld: str, trademark_hits: Sequence[str]) -> str:
    if not trademark_hits:
        return "low"
    sld_norm = _normalize_token(sld)
    for term in trademark_hits:
        if sld_norm == term or sld_norm.startswith(term) or sld_norm.endswith(term):
            return "high"
    return "medium"


def _keyword_residual(sld: str, keyword_hits: Sequence[str]) -> str:
    residual = _normalize_token(sld)
    for keyword in sorted(set(keyword_hits), key=lambda item: (-len(item), item)):
        residual = residual.replace(keyword, "", 1)
    return residual


def score_domain(
    record: DomainRecord,
    trademark_terms: Set[str],
    reference_words: Optional[Set[str]] = None,
) -> DomainScore:
    known_words = set(reference_words or ())
    domain = record.domain
    sld, tld = _split_domain(domain)
    sld_norm = _normalize_token(sld)
    length = len(sld)
    alpha = "".join(char for char in sld if char.isalpha())
    vowel_count = sum(1 for char in alpha if char in VOWELS)
    vowel_ratio = (float(vowel_count) / float(len(alpha))) if alpha else 0.0
    consonant_run = _max_consonant_run(alpha)
    repeat_run = _max_repeated_char_run(sld)
    has_hyphen = "-" in sld
    has_digit = any(char.isdigit() for char in sld)
    suffix_penalty = next((suffix for suffix in LOW_LIQUIDITY_SUFFIXES if sld.endswith(suffix)), None)

    commercial_hits = _keyword_hits(sld, COMMERCIAL_KEYWORDS)
    trend_hits = _keyword_hits(sld, TREND_KEYWORDS)
    keyword_union = sorted(set(commercial_hits + trend_hits))
    keyword_residual = _keyword_residual(sld, keyword_union) if keyword_union else sld_norm
    trademark_hits = _trademark_hits(sld, trademark_terms)
    trademark_risk = _trademark_risk_level(sld, trademark_hits)

    commercial_points = min(20, sum(COMMERCIAL_KEYWORDS[item] for item in commercial_hits))
    trend_points = min(60, sum(TREND_KEYWORDS[item] for item in trend_hits))
    lexical_bonus = 0
    if keyword_union and known_words and len(keyword_residual) >= 3:
        if keyword_residual in known_words:
            lexical_bonus = 6
        elif keyword_residual.endswith("s") and keyword_residual[:-1] in known_words:
            lexical_bonus = 3

    charset_points = 0
    if not has_hyphen:
        charset_points += 10
    if not has_digit:
        charset_points += 10
    if sld.isalpha():
        charset_points += 5

    pronounce_points = 0
    if 0.28 <= vowel_ratio <= 0.62:
        pronounce_points += 10
    elif 0.20 <= vowel_ratio <= 0.72:
        pronounce_points += 4
    else:
        pronounce_points -= 8

    if consonant_run <= 3:
        pronounce_points += 8
    elif consonant_run == 4:
        pronounce_points += 2
    else:
        pronounce_points -= 8

    if repeat_run <= 2:
        pronounce_points += 3
    else:
        pronounce_points -= 5

    risk_points = 0
    risks: List[str] = []
    if has_hyphen:
        risk_points += 8
        risks.append("contains hyphen")
    if has_digit:
        risk_points += 10
        risks.append("contains digits")
    if suffix_penalty and length >= 10:
        risk_points += 12
        risks.append(f"long inflected ending (-{suffix_penalty})")
    if consonant_run >= 5:
        risk_points += 8
        risks.append("hard-to-pronounce consonant cluster")
    if length >= 15:
        risk_points += 8
        risks.append("very long second-level domain")
    disallowed_hits = [term for term in DISALLOWED_TERMS if term in sld_norm]
    if disallowed_hits:
        risk_points += 30
        risks.append(f"contains restricted content term(s): {', '.join(disallowed_hits)}")
    if keyword_union:
        if len(keyword_residual) == 1:
            risk_points += 12
            risks.append("keyword-heavy composition with weak residual stem")
        elif len(keyword_residual) == 2 and keyword_residual not in {"io", "go", "up", "hq", "my", "xr"}:
            risk_points += 6
            risks.append("keyword-heavy composition with thin residual stem")
        if keyword_residual.endswith(("ed", "ing", "ly", "ment", "tion", "ness", "able", "ible")):
            risk_points += 8
            risks.append("residual stem looks grammatical, less brandable")
        if known_words and len(keyword_residual) >= 4 and lexical_bonus == 0:
            risk_points += 8
            risks.append("residual stem is uncommon in high-frequency English")
    if trademark_risk == "high":
        risk_points += 35
        risks.append(f"high trademark conflict risk ({', '.join(trademark_hits)})")
    elif trademark_risk == "medium":
        risk_points += 15
        risks.append(f"possible trademark conflict ({', '.join(trademark_hits)})")

    resale_score = _clamp(
        _length_points(length) + charset_points + pronounce_points + commercial_points + lexical_bonus - risk_points
    )
    future_score = _clamp(
        trend_points + min(15, commercial_points + lexical_bonus) + _brevity_points(length) - int(risk_points * 0.35)
    )

    score = _clamp(
        (resale_score * 0.70)
        + (future_score * 0.30)
        + (1.5 * min(2, max(0, record.source_file_count - 1)))
    )

    strengths: List[str] = []
    if length <= 10:
        strengths.append("short length")
    if not has_hyphen and not has_digit:
        strengths.append("clean spelling")
    if 0.28 <= vowel_ratio <= 0.62 and consonant_run <= 3:
        strengths.append("pronounceable")
    if commercial_hits:
        strengths.append(f"commercial keyword(s): {', '.join(commercial_hits)}")
    if trend_hits:
        strengths.append(f"future-trend keyword(s): {', '.join(trend_hits)}")
    if keyword_union and len(keyword_residual) >= 3:
        strengths.append(f"distinct residual stem: {keyword_residual}")
    if lexical_bonus > 0:
        strengths.append("residual stem appears in high-frequency English vocabulary")
    if record.source_file_count > 1:
        strengths.append(f"repeated across {record.source_file_count} source files")
    if trademark_risk == "low":
        strengths.append("no trademark conflicts detected against local blocklist")

    if not risks:
        risks.append("no major structure risks detected")

    if score >= 80:
        tier = "premium"
    elif score >= 65:
        tier = "strong"
    elif score >= 50:
        tier = "speculative"
    else:
        tier = "low-liquidity"

    return DomainScore(
        domain=domain,
        sld=sld,
        tld=tld,
        sld_length=length,
        resale_score=round(resale_score, 2),
        future_score=round(future_score, 2),
        score=round(score, 2),
        tier=tier,
        keyword_hits=keyword_union,
        trademark_hits=list(trademark_hits),
        trademark_risk=trademark_risk,
        strengths=strengths,
        risks=risks,
        source_files=sorted(record.source_files),
        source_file_count=record.source_file_count,
        occurrences=record.occurrences,
        first_seen=record.first_seen,
    )


def _is_valid_domain(value: str) -> bool:
    return bool(DOMAIN_RE.match(value))


def resolve_input_files(input_dir: Path, glob_pattern: str, recursive: bool = False) -> List[Path]:
    if recursive:
        files = [path for path in input_dir.rglob(glob_pattern) if path.is_file()]
    else:
        files = [path for path in input_dir.glob(glob_pattern) if path.is_file()]
    return sorted(path.resolve() for path in files)


def scan_domain_files(paths: Sequence[Path]) -> Tuple[Dict[str, DomainRecord], List[FileScanStats]]:
    records: Dict[str, DomainRecord] = {}
    per_file_stats: List[FileScanStats] = []

    for path in paths:
        stats = FileScanStats(path=str(path))
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                stats.total_lines += 1
                candidate = raw_line.strip().lower()
                if not candidate:
                    stats.blank_lines += 1
                    continue
                if not _is_valid_domain(candidate):
                    stats.invalid_lines += 1
                    continue

                stats.valid_lines += 1
                existing = records.get(candidate)
                if existing is None:
                    existing = DomainRecord(
                        domain=candidate,
                        occurrences=1,
                        source_files={path.name},
                        first_seen_file=path.name,
                        first_seen_line=line_number,
                    )
                    records[candidate] = existing
                else:
                    existing.occurrences += 1
                    existing.source_files.add(path.name)
                    stats.duplicate_valid_lines += 1
        per_file_stats.append(stats)
    return records, per_file_stats


def rank_domains(
    records: Iterable[DomainRecord],
    trademark_terms: Set[str],
    reference_words: Optional[Set[str]] = None,
) -> List[DomainScore]:
    ranked = [
        score_domain(record, trademark_terms=trademark_terms, reference_words=reference_words) for record in records
    ]
    ranked.sort(
        key=lambda item: (
            -item.score,
            item.trademark_risk,
            -item.resale_score,
            -item.future_score,
            item.sld_length,
            item.domain,
        )
    )
    return ranked


def ensure_unique_path(path: Path) -> Path:
    candidate = path
    suffix = candidate.suffix
    stem = candidate.stem
    index = 2
    while candidate.exists():
        candidate = candidate.with_name(f"{stem}-{index}{suffix}")
        index += 1
    return candidate


def write_ranked_csv(path: Path, ranked: Sequence[DomainScore]) -> Path:
    fieldnames = [
        "rank",
        "domain",
        "score",
        "resale_score",
        "future_score",
        "tier",
        "sld_length",
        "keyword_hits",
        "trademark_hits",
        "trademark_risk",
        "strengths",
        "risks",
        "source_file_count",
        "occurrences",
        "first_seen",
        "source_files",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, item in enumerate(ranked, start=1):
            writer.writerow(item.to_csv_row(index))
    return path


def write_top_txt(path: Path, ranked: Sequence[DomainScore]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(item.domain for item in ranked)
    if content:
        content += "\n"
    path.write_text(content, encoding="utf-8")
    return path


def _aggregate_stats(per_file_stats: Sequence[FileScanStats]) -> Dict[str, int]:
    total_lines = sum(item.total_lines for item in per_file_stats)
    blank_lines = sum(item.blank_lines for item in per_file_stats)
    invalid_lines = sum(item.invalid_lines for item in per_file_stats)
    valid_lines = sum(item.valid_lines for item in per_file_stats)
    duplicate_valid_lines = sum(item.duplicate_valid_lines for item in per_file_stats)
    return {
        "total_lines": total_lines,
        "blank_lines": blank_lines,
        "invalid_lines": invalid_lines,
        "valid_lines": valid_lines,
        "duplicate_valid_lines": duplicate_valid_lines,
    }


def write_summary_json(
    path: Path,
    *,
    input_dir: Path,
    glob_pattern: str,
    recursive: bool,
    files_scanned: Sequence[Path],
    per_file_stats: Sequence[FileScanStats],
    ranked_count: int,
    unique_domain_count: int,
    shortlist_count: int,
    min_score: float,
    top_limit: int,
    all_csv: Path,
    top_csv: Path,
    top_txt: Path,
    trademark_filter_enabled: bool,
    allow_medium_trademark_risk: bool,
    trademark_term_count: int,
    trademark_risk_counts: Dict[str, int],
) -> Path:
    aggregate = _aggregate_stats(per_file_stats)
    coverage_ok = aggregate["total_lines"] == (
        aggregate["blank_lines"] + aggregate["invalid_lines"] + aggregate["valid_lines"]
    )
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "input": {
            "input_dir": str(input_dir),
            "glob": glob_pattern,
            "recursive": bool(recursive),
            "files_scanned": [str(path) for path in files_scanned],
        },
        "coverage": {
            **aggregate,
            "coverage_ok": coverage_ok,
            "unique_valid_domains": unique_domain_count,
            "unique_ratio": round(
                float(unique_domain_count) / float(max(1, aggregate["valid_lines"])),
                6,
            ),
        },
        "ranking": {
            "ranked_count": ranked_count,
            "shortlist_count": shortlist_count,
            "min_score": float(min_score),
            "top_limit": int(top_limit),
            "trademark_filter_enabled": bool(trademark_filter_enabled),
            "allow_medium_trademark_risk": bool(allow_medium_trademark_risk),
            "trademark_term_count": int(trademark_term_count),
            "trademark_risk_counts": dict(trademark_risk_counts),
        },
        "outputs": {
            "all_csv": str(all_csv),
            "top_csv": str(top_csv),
            "top_txt": str(top_txt),
        },
        "file_stats": [item.to_dict() for item in per_file_stats],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _sanitize_prefix(prefix: str) -> str:
    safe_prefix = re.sub(r"[^a-z0-9._-]+", "-", prefix.strip().lower()).strip("-._")
    return safe_prefix or DEFAULT_PREFIX


def _build_output_paths(
    output_dir: Path,
    prefix: str,
) -> Tuple[Path, Path, Path, Path]:
    safe_prefix = _sanitize_prefix(prefix)
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d-%H-%M-%S")
    all_csv = ensure_unique_path(output_dir / f"{safe_prefix}-all-{timestamp}.csv")
    top_csv = ensure_unique_path(output_dir / f"{safe_prefix}-top-{timestamp}.csv")
    top_txt = ensure_unique_path(output_dir / f"{safe_prefix}-top-{timestamp}.txt")
    summary_json = ensure_unique_path(output_dir / f"{safe_prefix}-summary-{timestamp}.json")
    return all_csv, top_csv, top_txt, summary_json


def _select_shortlist(
    ranked: Sequence[DomainScore],
    top: int,
    min_score: float,
    trademark_filter_enabled: bool,
    allow_medium_trademark_risk: bool,
) -> List[DomainScore]:
    if top <= 0:
        return []
    filtered = [item for item in ranked if item.score >= min_score]
    if trademark_filter_enabled:
        allowed_risks = {"low", "medium"} if allow_medium_trademark_risk else {"low"}
        filtered = [item for item in filtered if item.trademark_risk in allowed_risks]
    return filtered[:top]


def _count_trademark_risks(ranked: Sequence[DomainScore]) -> Dict[str, int]:
    counts = {"low": 0, "medium": 0, "high": 0}
    for item in ranked:
        counts[item.trademark_risk] = counts.get(item.trademark_risk, 0) + 1
    return counts


def run(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else input_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.exists():
        print(f"Input directory not found: {input_dir}")
        return 2
    if not input_dir.is_dir():
        print(f"Input path is not a directory: {input_dir}")
        return 2
    if args.top < 1:
        print("--top must be >= 1")
        return 2
    if not (0 <= float(args.min_score) <= 100):
        print("--min-score must be in range [0, 100]")
        return 2

    trademark_terms = {_normalize_token(term) for term in DEFAULT_TRADEMARK_TERMS if _normalize_token(term)}
    if args.trademark_blocklist:
        blocklist_path = Path(args.trademark_blocklist).expanduser().resolve()
        if not blocklist_path.exists():
            print(f"Trademark blocklist file not found: {blocklist_path}")
            return 2
        trademark_terms.update(load_trademark_terms(blocklist_path))

    trademark_filter_enabled = not bool(args.disable_trademark_filter)
    allow_medium_trademark_risk = bool(args.allow_medium_trademark_risk)
    reference_words: Set[str] = set()
    if DEFAULT_REFERENCE_WORDLIST.exists():
        reference_words = load_reference_words(DEFAULT_REFERENCE_WORDLIST)

    files = resolve_input_files(input_dir, str(args.glob), recursive=bool(args.recursive))
    output_prefix = _sanitize_prefix(str(args.prefix))
    files = [path for path in files if not path.name.startswith(f"{output_prefix}-")]
    if not files:
        print(f"No files matched '{args.glob}' in {input_dir}")
        return 2

    records, per_file_stats = scan_domain_files(files)
    ranked = rank_domains(
        records.values(),
        trademark_terms=trademark_terms,
        reference_words=reference_words,
    )
    shortlist = _select_shortlist(
        ranked,
        top=int(args.top),
        min_score=float(args.min_score),
        trademark_filter_enabled=trademark_filter_enabled,
        allow_medium_trademark_risk=allow_medium_trademark_risk,
    )
    trademark_risk_counts = _count_trademark_risks(ranked)

    all_csv_path, top_csv_path, top_txt_path, summary_json_path = _build_output_paths(output_dir, str(args.prefix))

    write_ranked_csv(all_csv_path, ranked)
    write_ranked_csv(top_csv_path, shortlist)
    write_top_txt(top_txt_path, shortlist)
    write_summary_json(
        summary_json_path,
        input_dir=input_dir,
        glob_pattern=str(args.glob),
        recursive=bool(args.recursive),
        files_scanned=files,
        per_file_stats=per_file_stats,
        ranked_count=len(ranked),
        unique_domain_count=len(records),
        shortlist_count=len(shortlist),
        min_score=float(args.min_score),
        top_limit=int(args.top),
        all_csv=all_csv_path,
        top_csv=top_csv_path,
        top_txt=top_txt_path,
        trademark_filter_enabled=trademark_filter_enabled,
        allow_medium_trademark_risk=allow_medium_trademark_risk,
        trademark_term_count=len(trademark_terms),
        trademark_risk_counts=trademark_risk_counts,
    )

    aggregate = _aggregate_stats(per_file_stats)
    print(f"Scanned files: {len(files)}")
    print(f"Total lines read: {aggregate['total_lines']}")
    print(f"Valid domains: {aggregate['valid_lines']}")
    print(f"Invalid lines: {aggregate['invalid_lines']}")
    print(f"Blank lines: {aggregate['blank_lines']}")
    print(f"Unique valid domains: {len(records)}")
    print(f"Duplicate valid lines: {aggregate['duplicate_valid_lines']}")
    print(f"Ranked domains: {len(ranked)}")
    print(f"Reference vocabulary size: {len(reference_words)}")
    print(f"Trademark risk counts: {trademark_risk_counts}")
    print(
        f"Shortlist domains: {len(shortlist)} (top={args.top}, min_score={args.min_score}, "
        f"trademark_filter={trademark_filter_enabled}, allow_medium={allow_medium_trademark_risk})"
    )
    print(f"All ranked CSV: {all_csv_path}")
    print(f"Top ranked CSV: {top_csv_path}")
    print(f"Top ranked TXT: {top_txt_path}")
    print(f"Summary JSON: {summary_json_path}")

    print_limit = max(0, int(args.print_top))
    if print_limit > 0:
        print("")
        print("Top Candidates")
        for index, item in enumerate(shortlist[:print_limit], start=1):
            hits = ",".join(item.keyword_hits) if item.keyword_hits else "-"
            tm_hits = ",".join(item.trademark_hits) if item.trademark_hits else "-"
            print(
                f"{index:>3}. {item.domain:<32} score={item.score:>6.2f} "
                f"resale={item.resale_score:>6.2f} future={item.future_score:>6.2f} "
                f"tier={item.tier:<12} tm={item.trademark_risk:<6} hits={hits} tm_hits={tm_hits}"
            )

    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
