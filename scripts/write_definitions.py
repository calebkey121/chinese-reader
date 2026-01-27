#!/usr/bin/env python3
"""
Merge an "incoming" vocabulary JSON into a master master_dict.json.

Schema (both incoming and master):
{
  "词": {
    "pinyin": ["cí"],
    "definitions": ["word; term"],
    "tags": ["hsk1", "noun"]
  },
  ...
}

Behavior:
- Creates master file if missing.
- For each incoming word:
    - Overwrites the master entry by default.
    - Optionally unions tags with existing tags (MERGE_TAGS=True).
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Any, List

# ---- hardcoded paths (edit if you want) ----
# Resolve relative paths from this script's directory so running from elsewhere still works.
SCRIPT_DIR = Path(__file__).resolve().parent

MASTER_PATH = (SCRIPT_DIR / "master_dict.json")
INCOMING_PATH = (SCRIPT_DIR / "incoming_definitions.json")  # e.g. hsk1_vocab_input.json
OUTPUT_PATH = MASTER_PATH  # write back to master_dict.json

# Write safely via a temp file then rename.
ATOMIC_WRITE = True
MERGE_TAGS = True
KEEP_EXISTING_IF_INCOMING_MISSING = True

def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(f"Invalid JSON in {path}: {e}")

def _save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if ATOMIC_WRITE:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
    else:
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

def _as_list(x) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(v) for v in x if v is not None]
    return [str(x)]

def merge(master: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    for word, inc_entry in incoming.items():
        if not isinstance(inc_entry, dict):
            continue

        existing = master.get(word, {})
        if not isinstance(existing, dict):
            existing = {}

        # base overwrite
        merged = dict(inc_entry)

        if KEEP_EXISTING_IF_INCOMING_MISSING:
            for key in ("pinyin", "definitions", "tags"):
                if (key not in merged or merged.get(key) is None) and key in existing:
                    merged[key] = existing[key]

        # Normalize list-ish fields and drop empty strings.
        merged["pinyin"] = [s for s in _as_list(merged.get("pinyin")) if str(s).strip()]
        merged["definitions"] = [s for s in _as_list(merged.get("definitions")) if str(s).strip()]
        merged["tags"] = [s for s in _as_list(merged.get("tags")) if str(s).strip()]

        # tags union
        if MERGE_TAGS:
            tags_existing = _as_list(existing.get("tags"))
            tags_incoming = _as_list(merged.get("tags"))
            merged["tags"] = sorted(set(tags_existing).union(tags_incoming))

        master[word] = merged

    return master

def main() -> None:
    master = _load_json(MASTER_PATH)
    incoming = _load_json(INCOMING_PATH)

    if not INCOMING_PATH.exists():
        raise SystemExit(f"Incoming file not found: {INCOMING_PATH}")

    if not isinstance(master, dict):
        raise SystemExit(f"Master file {MASTER_PATH} is not a JSON object (dict).")
    if not isinstance(incoming, dict):
        raise SystemExit(f"Incoming file {INCOMING_PATH} is not a JSON object (dict).")

    merged = merge(master, incoming)
    _save_json(OUTPUT_PATH, merged)

    print(f"Merged {len(incoming)} incoming entries into {OUTPUT_PATH}. Total entries: {len(merged)}.")

if __name__ == "__main__":
    main()
