import json
import os
import re
import sys
import urllib.request
from typing import Dict, List, Optional, Tuple

# ---------------------------
# Configuration (edit these)
# ---------------------------

MASTER_JSON_PATH = "./api/data/master_dict.json"
OUTPUT_JSON_PATH = "master_dict_with_ccedict.json"

# Prefer local file. Download once if you want.
CCEDICT_LOCAL_PATH = "cedict_ts.u8"
DOWNLOAD_IF_MISSING = True

# A common mirror. You can replace this with any reliable CC-CEDICT URL.
CCEDICT_DOWNLOAD_URL = "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz"
CCEDICT_GZ_PATH = "cedict_ts.u8.gz"

# Definition controls
MAX_OUTPUT_CHARS = 60          # hard cap for the final string
MAX_SENSES = 2                 # keep at most N senses
MIN_SENSE_LEN = 2              # ignore extremely short senses (after trimming)
DROP_PATTERNS = [
    r"^surname\s",
    r"^variant\s+of\s",
    r"^see\s+also\s",
    r"^old\s+variant\s+of\s",
    r"^archaic\s",
    r"^abbr\.\s",
    r"^CL:",
    r"^\(dialect\)",
    r"^erhua\s+form\s+of\s",
    r"^erhua\s+variant\s+of\s",
    r"^erhua\s+version\s+of\s",
    r"^variant\s+reading\s+of\s",
]
# ---------------------------

CEDICT_LINE_RE = re.compile(
    r"^(?P<trad>\S+)\s+(?P<simp>\S+)\s+\[(?P<pinyin>[^\]]+)\]\s+/(?P<defs>.+)/\s*$"
)

TONE_MARK_MAP = {
    # a
    "ā": ("a", "1"), "á": ("a", "2"), "ǎ": ("a", "3"), "à": ("a", "4"),
    # e
    "ē": ("e", "1"), "é": ("e", "2"), "ě": ("e", "3"), "è": ("e", "4"),
    # i
    "ī": ("i", "1"), "í": ("i", "2"), "ǐ": ("i", "3"), "ì": ("i", "4"),
    # o
    "ō": ("o", "1"), "ó": ("o", "2"), "ǒ": ("o", "3"), "ò": ("o", "4"),
    # u
    "ū": ("u", "1"), "ú": ("u", "2"), "ǔ": ("u", "3"), "ù": ("u", "4"),
    # ü
    "ǖ": ("v", "1"), "ǘ": ("v", "2"), "ǚ": ("v", "3"), "ǜ": ("v", "4"),
    "ü": ("v", ""),  # no tone mark
    # nasal tones sometimes appear with tone marks on n/m in some sources; ignore if present
}

def ensure_ccedict_present() -> None:
    if os.path.exists(CCEDICT_LOCAL_PATH):
        return
    if not DOWNLOAD_IF_MISSING:
        raise FileNotFoundError(
            f"Missing {CCEDICT_LOCAL_PATH}. Either download CC-CEDICT to that path "
            f"or set DOWNLOAD_IF_MISSING=True."
        )

    # Download gz
    print(f"Downloading CC-CEDICT (gz) from: {CCEDICT_DOWNLOAD_URL}")
    urllib.request.urlretrieve(CCEDICT_DOWNLOAD_URL, CCEDICT_GZ_PATH)

    # Decompress
    import gzip
    print(f"Decompressing to: {CCEDICT_LOCAL_PATH}")
    with gzip.open(CCEDICT_GZ_PATH, "rb") as f_in, open(CCEDICT_LOCAL_PATH, "wb") as f_out:
        f_out.write(f_in.read())

    # Cleanup gz
    try:
        os.remove(CCEDICT_GZ_PATH)
    except OSError:
        pass


def normalize_pinyin(p: str) -> str:
    """
    Normalize pinyin to a comparable form.
    - lowercase
    - collapse whitespace
    - convert tone marks to numbered tones where possible
    - accept either tone-mark style or numbered style
    - convert ü -> v
    Example:
      "yí xiàr" -> "yi2 xiar4"
      "de" -> "de"
      "dí" -> "di2"
    """
    p = p.strip().lower()
    p = re.sub(r"\s+", " ", p)

    # Convert tone marks to base vowel + tone number by syllable.
    # We'll do a simple pass: for each syllable, if it contains a marked vowel, strip marks and append tone number.
    syllables = p.split(" ")
    out_syllables = []
    for syl in syllables:
        # If already has a tone number at end, normalize ü->v and keep.
        if re.search(r"[1-5]$", syl):
            syl = syl.replace("ü", "v")
            out_syllables.append(syl)
            continue

        tone_num = ""
        chars = list(syl)
        new_chars = []
        for ch in chars:
            if ch in TONE_MARK_MAP:
                base, tone = TONE_MARK_MAP[ch]
                new_chars.append(base)
                if tone:
                    tone_num = tone
            else:
                new_chars.append(ch)

        # Convert any remaining ü
        syl_base = "".join(new_chars).replace("ü", "v")

        # For erhua forms like "xiar" or "hua'r" you may see apostrophes; strip them for matching consistency
        syl_base = syl_base.replace("'", "")

        if tone_num:
            out_syllables.append(syl_base + tone_num)
        else:
            out_syllables.append(syl_base)

    return " ".join(out_syllables)


def parse_ccedict(path: str) -> Dict[str, List[Tuple[str, List[str]]]]:
    """
    Returns index:
      headword (simp OR trad) -> list of (normalized_pinyin, senses_list)

    We index both simplified and traditional so lookups work regardless of which
    form appears in master_dict.

    senses_list is raw split on '/' (English-only in CC-CEDICT, but may contain
    embedded Chinese/CL notes that we trim later).
    """
    index: Dict[str, List[Tuple[str, List[str]]]] = {}

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            m = CEDICT_LINE_RE.match(line)
            if not m:
                continue

            trad = m.group("trad")
            simp = m.group("simp")
            pinyin_raw = m.group("pinyin")
            defs_raw = m.group("defs")

            senses = [s.strip() for s in defs_raw.split("/") if s.strip()]
            p_norm = normalize_pinyin(pinyin_raw)

            index.setdefault(simp, []).append((p_norm, senses))
            if trad != simp:
                index.setdefault(trad, []).append((p_norm, senses))

    return index


def is_drop_sense(s: str) -> bool:
    s2 = s.strip().lower()
    for pat in DROP_PATTERNS:
        if re.search(pat, s2):
            return True
    return False

def strip_chinese_and_pinyin_markers(s: str) -> str:
    """Remove any Chinese characters and common CC-CEDICT markers from a sense."""
    # Remove any CJK ideographs
    s = re.sub(r"[\u4e00-\u9fff]+", "", s)
    # Remove the CC-CEDICT alt form separator and any leftover pipes
    s = s.replace("|", " ")
    # Remove bracketed pinyin like [yi1 kuai4]
    s = re.sub(r"\[[^\]]*\]", "", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s

def trim_senses(senses: List[str]) -> List[str]:
    """
    Apply filtering and keep the first MAX_SENSES that look useful.

    Notes:
    - CC-CEDICT sometimes embeds extra info inside a single sense separated by ';'
      (e.g. 'morning; CL:個|个[ge4]'). We split on ';' so we can keep 'morning'
      and drop the classifier note.
    - We also strip any Chinese characters / [pinyin] markers to avoid leaking
      answers into English-only flashcards.
    """
    cleaned: List[str] = []

    # Expand any semicolon-separated sub-senses into individual candidates
    expanded: List[str] = []
    for s in senses:
        s = s.strip()
        if not s:
            continue
        parts = [p.strip() for p in re.split(r";\s*", s) if p.strip()]
        expanded.extend(parts if parts else [s])

    for s in expanded:
        s = s.strip()

        # Remove bracket-y extras commonly not useful in flashcards
        s = re.sub(r"\s*\(.*?\)\s*", " ", s).strip()
        s = re.sub(r"\s+", " ", s)

        # Drop classifier notes even when not at the beginning
        if "CL:" in s:
            # Keep only text before CL if present
            s = s.split("CL:", 1)[0].strip()

        # Strip any Chinese/pinyin markers to avoid leaking answers
        s = strip_chinese_and_pinyin_markers(s)

        if len(s) < MIN_SENSE_LEN:
            continue
        if is_drop_sense(s):
            continue

        cleaned.append(s)
        if len(cleaned) >= MAX_SENSES:
            break

    return cleaned


def build_definition_string(senses: List[str]) -> str:
    """
    Join senses into a medium-length definition string with a hard cap.
    """
    senses = trim_senses(senses)
    if not senses:
        return ""

    # Prefer "; " join (your current style)
    joined = "; ".join(senses)

    if len(joined) <= MAX_OUTPUT_CHARS:
        return joined

    # If too long, fall back to first sense only (trimmed)
    first = senses[0]
    if len(first) <= MAX_OUTPUT_CHARS:
        return first

    # If even first is too long, truncate with ellipsis
    return first[: max(0, MAX_OUTPUT_CHARS - 1)] + "…"


def pick_best_entry(entries: List[Tuple[str, List[str]]], target_pinyin_norm: str) -> Optional[List[str]]:
    """
    Choose the entry whose normalized pinyin matches the target.
    If multiple match, take the first (CC-CEDICT is generally ordered reasonably).
    """
    # Exact match
    for p_norm, senses in entries:
        if p_norm == target_pinyin_norm:
            return senses

    # Soft match: some sources use "de" without tone; if target is "de" and entry has "de5" or vice versa.
    def strip_neutral(p: str) -> str:
        return re.sub(r"5\b", "", p)

    t_soft = strip_neutral(target_pinyin_norm)
    for p_norm, senses in entries:
        if strip_neutral(p_norm) == t_soft:
            return senses

    return None


def main() -> None:
    ensure_ccedict_present()
    cedict_index = parse_ccedict(CCEDICT_LOCAL_PATH)

    with open(MASTER_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Expected master JSON to be a list of vocab objects.")

    missing_word = 0
    missing_pinyin_match = 0

    for obj in data:
        word = obj.get("word", "")
        pinyin = obj.get("pinyin", "")
        if not word or not isinstance(word, str):
            continue

        entries = cedict_index.get(word)
        if not entries:
            obj["ccedict_definition"] = ""
            missing_word += 1
            continue

        target_p_norm = normalize_pinyin(pinyin) if isinstance(pinyin, str) else ""
        senses = pick_best_entry(entries, target_p_norm)

        if senses is None:
            # Safer fallback: try to choose an entry that yields a clean (non-empty)
            # English-only definition after trimming. This avoids obvious failures like
            # choosing the wrong reading for polyphonic characters.
            best = ""
            for _p_norm, _senses in entries:
                candidate = build_definition_string(_senses)
                if candidate:
                    best = candidate
                    break
            obj["ccedict_definition"] = best
            missing_pinyin_match += 1
        else:
            obj["ccedict_definition"] = build_definition_string(senses)

    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Wrote: {OUTPUT_JSON_PATH}")
    print(f"Words missing in CC-CEDICT: {missing_word}")
    print(f"Words with no pinyin match (used fallback first-entry): {missing_pinyin_match}")
    if missing_pinyin_match:
        print("Tip: review those entries—polyphonic characters are the usual cause (e.g., 的 de vs dí).")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)