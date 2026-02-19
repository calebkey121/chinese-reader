#!/usr/bin/env python3
"""
Build master_dict.json from master_orig.csv by attaching CC-CEDICT definitions.

Input:  master_orig.csv with columns at least:
  id,hsk_band,hanzi,pinyin,part
(Extra columns are ignored.)

Output: master_dict.json with objects:
  {
    "word": "<hanzi>",
    "pinyin": "<pinyin from csv>",
    "hsk_band": "<1|2|...|6|7-9>",
    "hsk_bands": ["<1|2|...|6|7-9>", ...],
    "hsk_ids": [<int>, ...],
    "tags": ["<pos>", ...],
    "ccedict_definitions": ["def1", "def2", ...]
  }

Notes:
- Entries with the same (word, pinyin) are merged.
- `hsk_band` remains a single canonical band (earliest level) for compatibility.
- `hsk_bands` and `hsk_ids` preserve all source occurrences.

How it “queries CC-CEDICT”:
- downloads the latest CC-CEDICT file from MDBG once (recommended by CC-CEDICT),
- parses it locally,
- looks up each word by its *simplified* form.

Sources:
- MDBG CC-CEDICT download page / files.  [oai_citation:0‡mdbg.net](https://www.mdbg.net/chinese/dictionary?page=cedict&utm_source=chatgpt.com)
- CC-CEDICT recommends MDBG for releases.  [oai_citation:1‡cc-cedict.org](https://cc-cedict.org/editor/editor.php?handler=Download&utm_source=chatgpt.com)
- CC-CEDICT is under CC BY-SA 3.0 (make sure you attribute in your app/repo).  [oai_citation:2‡cc-cedict.org](https://cc-cedict.org/wiki/?utm_source=chatgpt.com)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --- hardcoded corrections/fallbacks for syllabus quirks ---

# Some rows can be corrupted in the PDF dump / CSV (romanized syllable ended up in `word`, POS ended up in `pinyin`,
# or a phrase got truncated). Patch them here before CC-CEDICT lookup.
CORRECTIONS = {
    # romanized -> correct hanzi + pinyin + POS tag
    "tí": {"word": "提", "pinyin": "tí", "tags": ["动"]},
    "yán": {"word": "盐", "pinyin": "yán", "tags": ["名"]},
    "cáng": {"word": "藏", "pinyin": "cáng", "tags": ["动"]},
    "mǒu": {"word": "某", "pinyin": "mǒu", "tags": ["代"]},
    "zhǎn": {"word": "盏", "pinyin": "zhǎn", "tags": ["量"]},

    # truncated phrases in the dump
    "心吊胆": {"word": "提心吊胆", "pinyin": "tíxīn-diàodǎn"},
    "相并论": {"word": "相提并论", "pinyin": "xiāngtí-bìnglùn"},
    "小琴": {"word": "小提琴", "pinyin": "xiǎotíqín"},
    "捉迷": {"word": "捉迷藏", "pinyin": "zhuōmícáng"},
}

# Simple learner-friendly English glosses for cases CC-CEDICT lookup/simplification misses.
# Keep these short; they are used only when `ccedict_definitions` would otherwise be empty.
FALLBACK_DEFS = {
    "好玩儿": ["fun", "interesting"],
    "了": ["(particle) indicates a change or a completed action"],
    "面条儿": ["noodles"],
    "些": ["some", "a few"],
    "一点儿": ["a little", "a bit"],
    "小孩儿": ["child", "kid"],
    "辆": ["(measure word) for vehicles"],
    "聊天儿": ["to chat", "to talk"],
    "一块儿": ["together", "at the same time"],
    "纸": ["paper"],
    "差点儿": ["almost", "nearly"],
    "份": ["(measure word) portion", "copy"],
    "干活儿": ["to work", "to do chores"],
    "棵": ["(measure word) for trees/plants"],
    "摄氏度": ["degree Celsius"],
    "体检": ["physical exam", "medical checkup"],
    "纪录": ["record"],
    "颗": ["(measure word) for small round things"],
    "玫瑰": ["rose"],
    "土豆": ["potato"],
    "嘴巴": ["mouth"],
    "大伙儿": ["everyone", "the group"],
    "下功夫": ["to put in effort", "to work hard"],
    "新媒体": ["new media"],
    "新能源": ["new energy", "renewable energy"],

    "标识": ["to mark", "identifier", "logo"],
    "不予": ["not to grant", "to refuse"],
    "部首": ["(Chinese character) radical"],
    "纯朴": ["simple", "honest"],
    "此致": ["(letter closing) sincerely"],
    "打盹儿": ["to doze", "to take a nap"],
    "得意扬扬": ["smug", "proud"],
    "精彩纷呈": ["spectacular", "full of variety"],
    "居于": ["to be located", "to occupy"],
    "口哨儿": ["whistle"],
    "老伴儿": ["(elderly) spouse"],
    "两翼": ["two wings"],
    "没准儿": ["maybe", "not sure"],
    "磨炼": ["to temper", "to toughen"],
    "纳闷儿": ["puzzled", "confused"],
    "人缘儿": ["popularity", "good relationships"],
    "泰斗": ["leading authority", "master"],
    "提心吊胆": ["anxious", "worried"],
    "玩意儿": ["thing", "gadget"],
    "望远镜": ["telescope"],
    "相提并论": ["to mention in the same breath", "to compare"],
    "小提琴": ["violin"],
    "压轴": ["finale", "highlight"],
    "致力于": ["to devote oneself to"],
    "捉迷藏": ["hide-and-seek"],
    "做证": ["to testify", "to give evidence"],
    "做主": ["to decide", "to be in charge"],
}

RE_ROMANIZED_WORD = re.compile(r"^[A-Za-zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜü\-]+$")
POS_TOKENS = {"名", "动", "形", "副", "代", "助", "量", "介", "连", "叹", "前缀", "后缀", "数", "数量"}

try:
    import requests
except ImportError:
    print("Missing dependency: requests. Install with: pip install requests", file=sys.stderr)
    raise


CEDICT_ZIP_URL = "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.zip"  #  [oai_citation:3‡mdbg.net](https://www.mdbg.net/chinese/dictionary?page=cedict&utm_source=chatgpt.com)
CEDICT_TXT_NAME = "cedict_ts.u8"  # contained in the zip (most common name)

RE_CEDICT_LINE = re.compile(
    r"^(?P<trad>\S+)\s+(?P<simp>\S+)\s+\[(?P<pinyin>[^\]]+)\]\s+/(?P<defs>.+)/$"
)

# Entries like "CL:個|个[ge4]" are useful but noisy for a learner-first gloss; drop by default.
DROP_DEF_PREFIXES = (
    "CL:",
)

# Common meta/gloss patterns that are often not the primary learner meaning.
LOW_VALUE_PATTERNS = (
    "surname ",
    "abbr.",
    "abbrev.",
    "variant of",
    "see also",
    "old variant",
    "archaic",
    "onom.",
    "(onom.)",
    "(loanword)",
    "kangxi radical",
    "radical",
    "also pr.",
    "also written",
    "erhua variant",
    "classifier",
    "unit of weight",
    "old)",
    "(old",
    "obsolete",
    "chemistry",
)

RE_LEADING_PAREN_TAG = re.compile(r"^\([^)]*\)\s*")

RE_ANY_CL = re.compile(r"\bCL:")
RE_SQUARE_BRACKET = re.compile(r"\[[^\]]+\]")

# Remove parenthetical chunks that are usually metadata rather than meaning.
RE_PAREN_CHUNK = re.compile(r"\(([^)]*)\)")


def strip_low_value_parentheticals(text: str) -> str:
    def repl(m: re.Match) -> str:
        inner = m.group(1).strip().lower()
        if not inner:
            return ""
        # If the parenthetical contains dictionary meta, drop it.
        if "cl:" in inner or "abbr" in inner or "variant" in inner or "also pr" in inner or "radical" in inner:
            return ""
        # If it contains tone refs like [ge4] (sometimes nested in parentheses), drop it.
        if "[" in inner or "]" in inner or "|" in inner:
            return ""
        return ""  # default: drop parentheticals entirely for maximum simplicity

    # Drop all parenthetical chunks (we keep the outer text)
    return RE_PAREN_CHUNK.sub(repl, text)

def simplify_cedict_definitions(defs: List[str], *, max_defs: int = 3) -> List[str]:
    """Simplify CC-CEDICT glosses for learner UI.

    - Split on semicolons so "to love; to like" becomes separate items.
    - Drop classifier-only lines (CL:...).
    - Remove leading parenthetical tags like "(coll.)".
    - Prefer shorter, non-meta glosses.
    """

    expanded: List[str] = []
    for d in defs:
        if not d:
            continue
        # Split on semicolons into separate candidate glosses
        for part in d.split(";"):
            s = part.strip()
            if not s:
                continue
            expanded.append(s)

    cleaned: List[str] = []
    for d in expanded:
        d = d.strip()
        if not d:
            continue

        # Drop any gloss that mentions CL: anywhere (not just prefix)
        if RE_ANY_CL.search(d):
            continue

        # Remove leading parenthetical tags like (coll.), (bound form), etc.
        d2 = RE_LEADING_PAREN_TAG.sub("", d).strip()
        if not d2:
            continue

        # Remove bracketed pinyin refs like [ge4]
        d2 = RE_SQUARE_BRACKET.sub("", d2).strip()

        # Aggressively strip parenthetical metadata (and, by default, all parentheticals)
        d2 = strip_low_value_parentheticals(d2).strip()

        # Remove stray quote fragments and unmatched punctuation common in CC-CEDICT examples
        d2 = d2.replace('"', "").replace("'", "").strip()

        # Collapse whitespace
        d2 = re.sub(r"\s+", " ", d2).strip()
        if not d2:
            continue

        # Drop meta-ish glosses
        low = d2.lower()
        if any(p in low for p in LOW_VALUE_PATTERNS):
            continue

        # Drop glosses that are mostly punctuation / ellipsis artifacts
        if len(re.sub(r"[a-zA-Z ]", "", d2)) > 0 and len(d2) <= 3:
            continue

        cleaned.append(d2)

    # Deduplicate while preserving order
    seen = set()
    uniq: List[str] = []
    for d in cleaned:
        if d in seen:
            continue
        seen.add(d)
        uniq.append(d)

    # Rank: penalize meta-ish senses and very long glosses
    def score(gloss: str) -> Tuple[int, int, int]:
        g = gloss.lower()
        meta_penalty = sum(1 for p in LOW_VALUE_PATTERNS if p in g)
        # Prefer fewer penalties, shorter strings, and earlier original order
        return (-meta_penalty, -min(len(gloss), 200), 0)

    # Stable sort by score (Python sort is stable)
    uniq.sort(key=score, reverse=True)

    return uniq[:max_defs]


def entry_quality_score(entry: "CedictEntry") -> Tuple[int, int, int]:
    """Score a CC-CEDICT entry for selecting the best entry among multiple readings.

    Higher is better.
    """
    simp_defs = simplify_cedict_definitions(entry.defs, max_defs=8)
    if not simp_defs:
        return (0, 0, 0)

    # Penalize entries where most senses look meta (surname/abbr/variant)
    meta = 0
    for g in simp_defs:
        gl = g.lower()
        if any(p in gl for p in LOW_VALUE_PATTERNS):
            meta += 1

    # Prefer more usable senses, fewer meta senses, and shorter primary gloss
    primary_len = len(simp_defs[0])
    return (len(simp_defs) - meta, -meta, -primary_len)

# --- pinyin normalization helpers (match HSK diacritics vs cedict tone numbers) ---

_TONE_MAP = {
    # a
    "ā": "a", "á": "a", "ǎ": "a", "à": "a",
    # e
    "ē": "e", "é": "e", "ě": "e", "è": "e",
    # i
    "ī": "i", "í": "i", "ǐ": "i", "ì": "i",
    # o
    "ō": "o", "ó": "o", "ǒ": "o", "ò": "o",
    # u
    "ū": "u", "ú": "u", "ǔ": "u", "ù": "u",
    # ü
    "ǖ": "v", "ǘ": "v", "ǚ": "v", "ǜ": "v", "ü": "v",
    # r-coloring sometimes appears as ǎr etc; handled by above maps
}

def normalize_pinyin_for_match(p: str) -> str:
    """
    Produce a loose key for matching:
    - lowercase
    - remove spaces and punctuation
    - strip tone marks and tone numbers
    - map ü to v
    """
    p = p.strip().lower()
    # replace diacritics
    p = "".join(_TONE_MAP.get(ch, ch) for ch in p)
    # remove tone numbers
    p = re.sub(r"\d", "", p)
    # remove spaces and non-letters
    p = re.sub(r"[^a-zv]", "", p)
    return p

@dataclass
class CedictEntry:
    simp: str
    pinyin_raw: str
    defs: List[str]

def download_if_needed(cache_dir: Path, force: bool = False) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / "cedict_1_0_ts_utf-8_mdbg.zip"

    if zip_path.exists() and not force:
        return zip_path

    print(f"Downloading CC-CEDICT from: {CEDICT_ZIP_URL}")
    resp = requests.get(CEDICT_ZIP_URL, timeout=60)
    resp.raise_for_status()
    zip_path.write_bytes(resp.content)
    return zip_path

def extract_cedict_txt(zip_path: Path, cache_dir: Path) -> Path:
    out_txt = cache_dir / CEDICT_TXT_NAME
    if out_txt.exists():
        return out_txt

    with zipfile.ZipFile(zip_path, "r") as z:
        # Try expected filename first; otherwise pick the first .u8/.txt entry
        names = z.namelist()
        target = None
        if CEDICT_TXT_NAME in names:
            target = CEDICT_TXT_NAME
        else:
            for n in names:
                if n.endswith(".u8") or n.endswith(".txt"):
                    target = n
                    break
        if target is None:
            raise RuntimeError(f"Could not find CEDICT text inside zip. Files: {names[:20]}")

        with z.open(target) as f_in:
            out_txt.write_bytes(f_in.read())

    return out_txt

def load_cedict_index(cedict_txt_path: Path) -> Dict[str, List[CedictEntry]]:
    """
    Index CC-CEDICT by simplified word -> list of entries.
    """
    index: Dict[str, List[CedictEntry]] = {}

    with cedict_txt_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = RE_CEDICT_LINE.match(line)
            if not m:
                continue

            simp = m.group("simp")
            pinyin_raw = m.group("pinyin").strip()
            defs_raw = m.group("defs").strip()

            # split /.../ into defs (already captured without trailing slash)
            defs = [d.strip() for d in defs_raw.split("/") if d.strip()]

            index.setdefault(simp, []).append(CedictEntry(simp=simp, pinyin_raw=pinyin_raw, defs=defs))

    return index

def choose_best_entry(entries: List[CedictEntry], hsk_pinyin: str) -> Optional[CedictEntry]:
    """Choose the best CC-CEDICT entry.

    Strategy:
    1) Prefer exact pinyin match after normalization.
    2) Within that subset (or overall if none match), pick the highest-quality entry
       (avoid surname/abbr/variant-only entries when a common meaning exists).
    """
    if not entries:
        return None
    if len(entries) == 1:
        return entries[0]

    target = normalize_pinyin_for_match(hsk_pinyin)

    candidates = entries
    if target:
        matched = [e for e in entries if normalize_pinyin_for_match(e.pinyin_raw) == target]
        if matched:
            candidates = matched

    # Pick the best quality entry; stable max keeps first if tie
    best = max(candidates, key=entry_quality_score)
    return best

def pos_to_tags(pos_field: str) -> List[str]:
    """
    Convert your CSV 'part' field into a tag list.
    You can later expand mapping (e.g., 名->noun, 动->verb) if desired.
    For now, return the raw tokens split on common delimiters.
    """
    if not pos_field:
        return []
    s = pos_field.strip()
    # split on Chinese/English commas, ideographic comma, slashes
    parts = re.split(r"[、，,/\s]+", s)
    return [p for p in (p.strip() for p in parts) if p]


# --- correction helper for known missing/garbled entries ---
def apply_corrections(word: str, pinyin: str, tags: List[str]) -> Tuple[str, str, List[str]]:
    """Fix known corruption cases and romanized placeholders."""

    if word in CORRECTIONS:
        patch = CORRECTIONS[word]
        word = patch.get("word", word)
        pinyin = patch.get("pinyin", pinyin)
        if "tags" in patch and patch["tags"]:
            tags = patch["tags"]
        return word, pinyin, tags

    # If the 'word' is romanized and the 'pinyin' field is actually a POS token, swap in a corrected mapping if known.
    # Otherwise, keep as-is (it will likely land in missing output for manual review).
    if RE_ROMANIZED_WORD.match(word) and pinyin in POS_TOKENS and word in CORRECTIONS:
        patch = CORRECTIONS[word]
        word = patch.get("word", word)
        pinyin = patch.get("pinyin", pinyin)
        if "tags" in patch and patch["tags"]:
            tags = patch["tags"]

    return word, pinyin, tags

def band_rank(band: str) -> int:
    """Rank bands so we can sort/choose a canonical one when duplicates exist."""
    b = (band or "").strip()
    if b == "7-9":
        return 9
    m = re.match(r"^(\d+)$", b)
    if m:
        return int(m.group(1))
    return 99


def normalize_band_list(bands: List[str]) -> List[str]:
    """Deduplicate and sort bands by level (1..6, 7-9)."""
    uniq: List[str] = []
    for b in bands:
        b2 = (b or "").strip()
        if not b2:
            continue
        if b2 not in uniq:
            uniq.append(b2)
    uniq.sort(key=band_rank)
    return uniq


def merge_items(a: dict, b: dict) -> dict:
    """Merge two entries with the same (word, pinyin)."""
    out = dict(a)

    # Merge hsk_ids
    ids: List[int] = []
    for src in (a.get("hsk_ids"), b.get("hsk_ids")):
        if isinstance(src, list):
            for x in src:
                if isinstance(x, int):
                    ids.append(x)
                elif isinstance(x, str) and x.isdigit():
                    ids.append(int(x))
        elif isinstance(src, int):
            ids.append(src)
        elif isinstance(src, str) and src.isdigit():
            ids.append(int(src))
    out["hsk_ids"] = sorted(set(ids))

    # Merge original surface spellings from the syllabus (e.g., 本1, 本2)
    surfaces: List[str] = []
    for src in (a.get("hsk_surfaces"), b.get("hsk_surfaces")):
        if isinstance(src, list):
            for s in src:
                if isinstance(s, str) and s and s not in surfaces:
                    surfaces.append(s)
    out["hsk_surfaces"] = surfaces

    # Merge bands; keep canonical band as the earliest level
    bands: List[str] = []
    for src in (a.get("hsk_bands"), b.get("hsk_bands")):
        if isinstance(src, list):
            bands.extend([str(x) for x in src])
    for src in (a.get("hsk_band"), b.get("hsk_band")):
        if isinstance(src, str) and src.strip():
            bands.append(src.strip())

    bands = normalize_band_list(bands)
    out["hsk_bands"] = bands
    out["hsk_band"] = bands[0] if bands else ""

    # Merge tags (dedupe preserving order)
    tags: List[str] = []
    for tlist in (a.get("tags"), b.get("tags")):
        if isinstance(tlist, list):
            for t in tlist:
                if isinstance(t, str) and t and t not in tags:
                    tags.append(t)
    out["tags"] = tags

    # Merge definitions (union unique preserving order)
    defs: List[str] = []
    for dlist in (a.get("ccedict_definitions"), b.get("ccedict_definitions")):
        if isinstance(dlist, list):
            for d in dlist:
                if isinstance(d, str) and d and d not in defs:
                    defs.append(d)
    out["ccedict_definitions"] = defs

    return out


def build_master_json(
    csv_path: Path,
    cedict_index: Dict[str, List[CedictEntry]],
) -> Tuple[List[dict], List[dict]]:
    merged: Dict[Tuple[str, str], dict] = {}

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = {"hanzi", "pinyin", "hsk_band"}
        if not required.issubset(reader.fieldnames or []):
            raise RuntimeError(f"CSV must include columns: {sorted(required)}. Got: {reader.fieldnames}")

        for row in reader:
            word = (row.get("hanzi") or "").strip()
            pinyin = (row.get("pinyin") or "").strip()
            band = (row.get("hsk_band") or "").strip()
            pos = (row.get("part") or "").strip()

            hsk_id_raw = (row.get("id") or "").strip()
            hsk_id: Optional[int] = int(hsk_id_raw) if hsk_id_raw.isdigit() else None

            tags = pos_to_tags(pos)
            # Fix known corruption cases before lookup
            word, pinyin, tags = apply_corrections(word, pinyin, tags)

            # Normalize syllabus disambiguators like "本1"/"点1" -> "本"/"点" so they merge.
            original_word = word
            normalized_word = re.sub(r"\d+$", "", word)
            word = normalized_word

            defs: List[str] = []

            # Use normalized word for lookup
            lookup_word = word

            # Try a small set of lookup variants (erhua forms often appear with 儿 in HSK but not in CC-CEDICT)
            lookup_candidates = [lookup_word]
            if "儿" in lookup_word:
                lookup_candidates.append(lookup_word.replace("儿", ""))
            # Also try stripping a trailing 儿 only
            if lookup_word.endswith("儿"):
                lookup_candidates.append(lookup_word[:-1])

            best = None
            for cand in lookup_candidates:
                entries = cedict_index.get(cand, [])
                best = choose_best_entry(entries, pinyin)
                if best:
                    break

            if best:
                # Provide a learner-friendly short list (still derived from CC-CEDICT)
                defs = simplify_cedict_definitions(best.defs, max_defs=3)

            # Final fallback: hardcoded simple glosses for a small curated list.
            if not defs:
                defs = FALLBACK_DEFS.get(word, FALLBACK_DEFS.get(lookup_word, []))

            item = {
                "word": word,
                "pinyin": pinyin,
                "hsk_band": band,
                "hsk_bands": ([band] if band else []),
                "hsk_ids": ([hsk_id] if hsk_id is not None else []),
                "hsk_surfaces": ([original_word] if original_word and original_word != word else []),
                "tags": tags,
                "ccedict_definitions": defs,
            }

            key = (item["word"], item["pinyin"])
            if key in merged:
                merged[key] = merge_items(merged[key], item)
            else:
                merged[key] = item

    out = list(merged.values())

    # stable sort by first (lowest) hsk_id if present, otherwise by word
    def sort_key(it: dict):
        ids = it.get("hsk_ids") or []
        first_id = ids[0] if isinstance(ids, list) and ids else 10**9
        return (first_id, it.get("word", ""))

    out.sort(key=sort_key)

    missing = [it for it in out if not (isinstance(it.get("ccedict_definitions"), list) and it.get("ccedict_definitions"))]

    return out, missing

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("master_orig_csv", type=Path, help="Path to master_orig.csv")
    ap.add_argument("--out", type=Path, default=Path("master_dict.json"), help="Output JSON path")
    ap.add_argument("--missing-out", type=Path, default=Path("missing_ccedict.json"), help="Words with no CC-CEDICT match")
    ap.add_argument("--cache-dir", type=Path, default=Path(".cache/cedict"), help="Cache directory for CC-CEDICT download")
    ap.add_argument("--force-download", action="store_true", help="Redownload CC-CEDICT even if cached")
    args = ap.parse_args()

    zip_path = download_if_needed(args.cache_dir, force=args.force_download)
    cedict_txt = extract_cedict_txt(zip_path, args.cache_dir)
    print(f"Using CC-CEDICT file: {cedict_txt}")

    cedict_index = load_cedict_index(cedict_txt)
    print(f"Indexed simplified entries: {len(cedict_index)}")

    master, missing = build_master_json(args.master_orig_csv, cedict_index)
    args.out.write_text(json.dumps(master, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.missing_out.write_text(json.dumps(missing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote: {args.out}  (items={len(master)})")
    print(f"Wrote: {args.missing_out}  (no CC-CEDICT match={len(missing)})")
    print("Note: CC-CEDICT is CC BY-SA 3.0; include attribution in your repo/app. See CC-CEDICT wiki.  [oai_citation:4‡cc-cedict.org](https://cc-cedict.org/wiki/?utm_source=chatgpt.com)")

if __name__ == "__main__":
    main()