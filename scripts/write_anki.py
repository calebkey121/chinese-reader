#!/usr/bin/env python3
"""
Update Anki deck notes from your master dict via AnkiConnect.

Rules:
- Deck: Chinese::Vocab
- Match notes by field Chinese == <word>  (exact match using Anki field search)
- Write hsk_definition  -> field Def1
- Write ccedict_definition -> field Def2
- If either definition is a list, join with '; ' (semicolon + space)
- If either is a string, write as-is (trimmed)
- Does NOT change the Chinese field.

Requirements:
- Anki desktop running
- AnkiConnect installed + enabled (default: http://127.0.0.1:8765)
"""

import json
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Union


# ---------------------------
# Hardcoded configuration
# ---------------------------
ANKI_CONNECT_URL = "http://127.0.0.1:8765"
DECK_NAME = "Chinese::Vocab"

CHINESE_FIELD = "Chinese"
DEF1_FIELD = "Def1"  # HSK
DEF2_FIELD = "Def2"  # CCEDICT

MASTER_DICT_PATH = "./api/data/master_dict.json"

# Only update notes where at least one of Def1/Def2 would change
SKIP_IF_NO_CHANGE = True

# If True, also update notes even if Def1/Def2 currently have content
OVERWRITE_EXISTING = True

# Batch sizes (avoid huge payloads)
BATCH_FIND = 200
BATCH_GETINFO = 200
BATCH_UPDATE = 200
# ---------------------------


JsonVal = Union[str, List[str], None]


def anki_request(action: str, params: Optional[Dict[str, Any]] = None) -> Any:
    payload = {"action": action, "version": 6, "params": params or {}}
    req = urllib.request.Request(
        ANKI_CONNECT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError(
            "Failed to reach AnkiConnect. Is Anki running and AnkiConnect installed?"
        ) from e

    if "error" not in out or "result" not in out:
        raise RuntimeError(f"Unexpected AnkiConnect response: {out}")
    if out["error"] is not None:
        raise RuntimeError(f"AnkiConnect error for {action}: {out['error']}")
    return out["result"]


def join_defs(val: JsonVal) -> str:
    if val is None:
        return ""
    if isinstance(val, list):
        parts = []
        for x in val:
            if isinstance(x, str):
                s = " ".join(x.split()).strip()
                if s:
                    parts.append(s)
        return "; ".join(parts)
    if isinstance(val, str):
        return " ".join(val.split()).strip()
    return ""


def escape_for_anki_field_query(s: str) -> str:
    """
    Escape double-quotes for Anki's search syntax: field:"value"
    """
    return s.replace('"', r"\"")


def chunked(lst: List[Any], n: int) -> List[List[Any]]:
    return [lst[i : i + n] for i in range(0, len(lst), n)]


def main() -> None:
    # Quick connectivity check
    anki_request("version")

    with open(MASTER_DICT_PATH, "r", encoding="utf-8") as f:
        master = json.load(f)
    if not isinstance(master, list):
        raise ValueError("Master dict must be a JSON list of vocab objects.")

    total_words = 0
    total_notes_found = 0
    total_notes_updated = 0
    total_words_no_note = 0

    for obj in master:
        if not isinstance(obj, dict):
            continue

        word = obj.get("word", "")
        if not isinstance(word, str) or not word:
            continue

        # definitions -> strings joined by '; '
        hsk_def = join_defs(obj.get("hsk_definition"))
        cced_def = join_defs(obj.get("ccedict_definition"))

        # If you want to avoid writing empties, keep this guard.
        # (If you *do* want to blank fields when missing, remove this.)
        if not hsk_def and not cced_def:
            continue

        total_words += 1

        # Find notes in deck with exact Chinese field match
        q_word = escape_for_anki_field_query(word)
        query = f'deck:"{DECK_NAME}" {CHINESE_FIELD}:"{q_word}"'
        note_ids: List[int] = anki_request("findNotes", {"query": query}) or []

        if not note_ids:
            total_words_no_note += 1
            continue

        total_notes_found += len(note_ids)

        # Get current fields so we can skip unchanged
        for batch in chunked(note_ids, BATCH_GETINFO):
            notes_info = anki_request("notesInfo", {"notes": batch})

            updates = []
            for note in notes_info:
                note_id = note.get("noteId")
                fields = (note.get("fields") or {})
                cur_def1 = (fields.get(DEF1_FIELD, {}) or {}).get("value", "")
                cur_def2 = (fields.get(DEF2_FIELD, {}) or {}).get("value", "")

                cur_def1 = " ".join(str(cur_def1).split()).strip()
                cur_def2 = " ".join(str(cur_def2).split()).strip()

                new_def1 = hsk_def
                new_def2 = cced_def

                if not OVERWRITE_EXISTING:
                    # Only fill if empty
                    if cur_def1:
                        new_def1 = cur_def1
                    if cur_def2:
                        new_def2 = cur_def2

                if SKIP_IF_NO_CHANGE and new_def1 == cur_def1 and new_def2 == cur_def2:
                    continue

                updates.append(
                    {
                        "id": note_id,
                        "fields": {
                            DEF1_FIELD: new_def1,
                            DEF2_FIELD: new_def2,
                        },
                    }
                )

            # Send updates (AnkiConnect updateNoteFields accepts a single "note" object)
            for up_batch in chunked(updates, BATCH_UPDATE):
                if not up_batch:
                    continue
                for note_update in up_batch:
                    anki_request("updateNoteFields", {"note": note_update})
                total_notes_updated += len(up_batch)

    print("Done.")
    print(f"Words processed (non-empty defs): {total_words}")
    print(f"Words with no matching note: {total_words_no_note}")
    print(f"Total notes matched: {total_notes_found}")
    print(f"Total notes updated: {total_notes_updated}")


if __name__ == "__main__":
    main()