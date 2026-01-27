#!/usr/bin/env python3
"""
Filter vocab JSON entries where:
A) the headword key is exactly 1 character, and
B) the entry has tags (optionally: must include an HSK tag).

Input JSON shape:
{
  "一": {"pinyin":[...], "definitions":[...], "tags":["hsk1","numeral"]},
  "一个": {"pinyin":[...], "definitions":[...]}
}
"""

import json
from pathlib import Path

# ---- config (edit these) ----
INPUT_PATH = "./api/data/master_dict.json"          # your full JSON file
OUTPUT_PATH = "one_char_tagged.txt"
REQUIRE_HSK_TAG = True             # True = must have a tag like "hsk1"; False = any tags
# -----------------------------

def has_hsk_tag(tags) -> bool:
    return any(isinstance(t, str) and t.lower().startswith("hsk") for t in tags)

def main() -> None:
    data = json.loads(Path(INPUT_PATH).read_text(encoding="utf-8"))

    matched = []
    for word, info in data.items():
        # A) exactly one character headword
        if not isinstance(word, str) or len(word) != 1:
            continue

        # B) has tags
        if not isinstance(info, dict):
            continue
        tags = info.get("tags")
        if not isinstance(tags, list) or len(tags) == 0:
            continue

        if REQUIRE_HSK_TAG and not has_hsk_tag(tags):
            continue

        matched.append(word)

    # stable, readable output
    matched = sorted(set(matched))

    Path(OUTPUT_PATH).write_text(
        "\n".join(matched) + ("\n" if matched else ""),
        encoding="utf-8",
    )

    # quick summary
    print(f"Loaded: {len(data)} entries")
    print(f"Matched: {len(matched)} entries")
    print(f"Wrote: {OUTPUT_PATH}")

    # optional: show matched keys
    # print("Matches:", " ".join(sorted(out.keys())))

if __name__ == "__main__":
    main()