#!/usr/bin/env python3
"""
Parse the raw HSK PDF text dump into:
  1) cleaned_v2.txt  (one entry per line, robust to "invisible newlines")
  2) out.csv         (id,hsk_band,hanzi,pinyin,part)
  3) rejects.txt     (entries we couldn't confidently parse)

Key idea:
- DO NOT trust line breaks at all. Treat the entire file as a token stream.
- Reconstruct records by the pattern:  <id:int> <band> <hanzi...> <pinyin...> <pos...>
- Strip watermark/header/page-noise anywhere it appears.

This directly targets issues visible in your raw dump (e.g., watermark blocks and header reappearing mid-stream).  [oai_citation:0‡orig.txt](sediment://file_00000000971071fd86d4bbebe4669a93)

Usage:
  python3 hsk_parse_v2.py /mnt/data/orig.txt out.csv cleaned_v2.txt rejects.txt

If you omit output args:
  out.csv, cleaned_v2.txt, rejects.txt will be created in the current directory.
"""

from __future__ import annotations
import csv
import re
import sys
from pathlib import Path
from dataclasses import dataclass

# -----------------------------
# Config: junk removal
# -----------------------------

# Remove these phrases anywhere (even glued mid-line)
JUNK_PHRASES = [
    r"汉考国际",
    r"中外语言交流合作中心\s*发布",
    r"HSK\s*考试大纲",
    r"Syllabus\s*for\s*the\s*Chinese\s*Proficiency\s*Test",
    r"序号\s*等级\s*词语\s*拼音\s*词性",
    r"序号等级词语拼音词性",
    r"中文水平考试",
    r"中\s*文\s*水\s*平\s*考\s*试",
    r"2025-11\s*发布\s*2026-07\s*实施",
]

JUNK_RE = re.compile("|".join(JUNK_PHRASES), flags=re.IGNORECASE)

# Page numbers often appear as isolated digits (e.g., "77") between blocks
RE_PAGE_NUMBER = re.compile(r"^\d{1,4}$")

# Entry start: ID is an integer token; band immediately follows.
# Supported band formats:
#   - "1", "2", ...
#   - "1（4）" / "1(4)"
#   - multiple alternates like "1（2）（4）" or "2（3）（5）"
#   - grouped bands like "7-9" (including cases that were spaced as "7 - 9" before normalization)
RE_INT = re.compile(r"^\d+$")
RE_BAND_TOKEN = re.compile(r"^(?:\d+(?:-\d+)?)(?:[（(]\s*[\d\-]+\s*[）)])*$")


def normalize_band_token(tok: str) -> str:
    """Return the base band as a string: '1'..'6' or '7-9'.

    Examples:
      '1' -> '1'
      '1（4）' -> '1'
      '1（2）（4）' -> '1'
      '7-9' -> '7-9'
      '2（7-9）' -> '2'
    """
    base = re.split(r"[（(]", tok, maxsplit=1)[0].strip()
    return base

# "pinyin-ish": latin letters or tone marks (covers diacritics and "shéi/shuí")
RE_PINYINISH = re.compile(r"[a-zA-Z]|[āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ]|/")

# POS detection (extendable)
POS_ROOTS = ["名", "动", "形", "副", "代", "助", "量", "介", "连", "叹", "前缀", "后缀", "数", "数量"]
RE_POS = re.compile("|".join(map(re.escape, POS_ROOTS)))

# Tokens that can appear inside POS blobs
RE_POS_PUNCT = re.compile(r"^[、，,/()（）\-\s]+$")

def normalize_text(raw: str) -> str:
    # Normalize newlines to spaces (this is the core fix for "invisible newlines")
    s = raw.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\n", " ")

    # Normalize grouped bands that may have spaces around the hyphen: "7 - 9" -> "7-9"
    s = re.sub(r"(\d)\s*-\s*(\d)", r"\1-\2", s)

    # Remove junk phrases anywhere
    s = JUNK_RE.sub(" ", s)

    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()

    return s

def tokenize(s: str) -> list[str]:
    # Simple whitespace tokenization after normalization is surprisingly effective for PDF dumps
    return s.split()

@dataclass
class Entry:
    id: int
    band: str
    hanzi: str
    pinyin: str
    part: str

def is_hanzi_token(tok: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", tok))

def looks_like_pos(tok: str) -> bool:
    return bool(RE_POS.search(tok))

def parse_tokens(tokens: list[str]) -> tuple[list[Entry], list[str]]:
    """
    Streaming parse:
      ID -> BAND -> HANZI (1+ CJK tokens) -> PINYIN (0+ pinyin tokens) -> POS (0+ tokens)
    Stops POS when next ID+BAND is detected.
    """
    entries: list[Entry] = []
    rejects: list[str] = []
    i = 0
    n = len(tokens)

    def peek(k: int) -> str | None:
        j = i + k
        return tokens[j] if 0 <= j < n else None

    def next_starts_entry(idx: int) -> bool:
        if idx + 1 >= n:
            return False
        return bool(RE_INT.match(tokens[idx])) and bool(RE_BAND_TOKEN.match(tokens[idx + 1]))

    while i < n:
        # Skip page numbers and obvious noise tokens
        if RE_PAGE_NUMBER.match(tokens[i]) and not next_starts_entry(i):
            i += 1
            continue

        if not next_starts_entry(i):
            i += 1
            continue

        raw_start = i

        entry_id = int(tokens[i]); i += 1
        band_token = tokens[i]; i += 1
        band = normalize_band_token(band_token)

        # HANZI: one or more CJK tokens; handle stray "国际" noise that appears alone before real hanzi
        hanzi_tokens: list[str] = []
        while i < n:
            t = tokens[i]

            # Heuristic: drop stray "国际" only when it's clearly not the hanzi term.
            # If the current token is "国际" AND the next token is CJK AND the token after that is pinyin-ish,
            # then "国际" is almost certainly garbage injected by the PDF watermarking/line-break.  [oai_citation:1‡orig.txt](sediment://file_00000000971071fd86d4bbebe4669a93)
            if t == "国际":
                t1 = peek(1)
                t2 = peek(2)
                if t1 and is_hanzi_token(t1) and t2 and RE_PINYINISH.search(t2):
                    i += 1
                    continue

            if is_hanzi_token(t):
                hanzi_tokens.append(t)
                i += 1
                continue

            break

        # If no hanzi tokens, reject this entry and resync
        if not hanzi_tokens:
            # resync: move forward one token past raw_start
            rejects.append(" ".join(tokens[raw_start:min(raw_start+40, n)]))
            i = raw_start + 1
            continue

        # PINYIN: tokens containing latin/tone marks; stop when POS starts OR next entry starts
        pinyin_tokens: list[str] = []
        while i < n:
            if next_starts_entry(i):
                break
            t = tokens[i]

            # Stop if we hit POS
            if looks_like_pos(t):
                break

            # Accept pinyin-ish tokens; otherwise stop (usually means POS is missing or formatting oddity)
            if RE_PINYINISH.search(t):
                pinyin_tokens.append(t)
                i += 1
                continue

            # If it's a page number, skip it
            if RE_PAGE_NUMBER.match(t):
                i += 1
                continue

            # Otherwise, stop pinyin
            break

        # POS: consume 1+ tokens that look like POS or POS punctuation, until next entry starts
        pos_tokens: list[str] = []
        while i < n:
            if next_starts_entry(i):
                break
            t = tokens[i]

            if looks_like_pos(t) or RE_POS_PUNCT.match(t):
                pos_tokens.append(t)
                i += 1
                continue

            # Skip page numbers inside the stream
            if RE_PAGE_NUMBER.match(t):
                i += 1
                continue

            # Not POS; stop
            break

        hanzi = "".join(hanzi_tokens)
        pinyin = " ".join(pinyin_tokens).strip()
        part = " ".join(pos_tokens).strip()

        entries.append(Entry(id=entry_id, band=band, hanzi=hanzi, pinyin=pinyin, part=part))

    return entries, rejects

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 hsk_parse_v2.py orig.txt [out.csv] [cleaned_v2.txt] [rejects.txt]")
        raise SystemExit(2)

    inp = Path(sys.argv[1])
    out_csv = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path("out.csv")
    out_clean = Path(sys.argv[3]) if len(sys.argv) >= 4 else Path("cleaned_v2.txt")
    out_rejects = Path(sys.argv[4]) if len(sys.argv) >= 5 else Path("rejects.txt")

    raw = inp.read_text(encoding="utf-8", errors="replace")
    norm = normalize_text(raw)
    tokens = tokenize(norm)

    entries, rejects = parse_tokens(tokens)

    # Write cleaned txt (one record per line, minimal noise)
    with out_clean.open("w", encoding="utf-8") as f:
        for e in entries:
            # Keep format similar to your earlier cleaned file
            f.write(f"{e.id} {e.band} {e.hanzi} {e.pinyin} {e.part}".strip() + "\n")

    # Write CSV
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "hsk_band", "hanzi", "pinyin", "part"])
        for e in entries:
            w.writerow([e.id, e.band, e.hanzi, e.pinyin, e.part])

    # Write rejects for manual inspection / rules tuning
    with out_rejects.open("w", encoding="utf-8") as f:
        for r in rejects:
            f.write(r.strip() + "\n")

    # Print quick stats (so you can see POS recovery improvements)
    missing_pos = sum(1 for e in entries if not e.part)
    print(f"tokens: {len(tokens)}")
    print(f"entries parsed: {len(entries)}")
    print(f"entries missing POS: {missing_pos}")
    print(f"reject snippets: {len(rejects)}")
    print(f"wrote: {out_clean}")
    print(f"wrote: {out_csv}")
    print(f"wrote: {out_rejects}")

if __name__ == "__main__":
    main()