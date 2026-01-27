#!/usr/bin/env python3
"""
anki_sync_progress.py

Pull vocab from an Anki deck via AnkiConnect and write a progress file you can
re-run anytime. A “learned” word is anything that is NOT in the New queue.

Requires:
- Anki desktop running
- AnkiConnect installed/enabled (default: http://127.0.0.1:8765)

What it writes:
- ../data/anki_progress.json (relative to this script)
  mapping term -> {status, learned, note_id, card_id, due, ivl}

You can later join this with master_dict.json by key.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

# --------- CONFIG (edit these) ---------
ANKICONNECT_URL = "http://127.0.0.1:8765"
DECK_NAME = "Chinese::Vocab"          # <-- your deck name
TERM_FIELD = "Chinese"      # <-- field that contains the vocab (e.g., "Front", "Simplified", "Word")
OUTPUT_PATH = Path("./api/data/anki_progress.json")
# --------------------------------------


def anki_request(action: str, params: dict[str, Any] | None = None) -> Any:
    payload = {"action": action, "version": 6, "params": params or {}}
    req = urllib.request.Request(
        ANKICONNECT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if data.get("error"):
        raise RuntimeError(f"AnkiConnect error for {action}: {data['error']}")
    return data.get("result")


def is_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def normalize_term(s: str) -> str:
    s = (s or "").strip()
    # remove HTML (Anki fields often contain <div>, <br>, etc.)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.strip()
    # keep only CJK + ASCII letters/digits; drop punctuation/spaces
    s = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s)
    return s


def status_from_queue_type(queue: int | None, typ: int | None) -> str:
    """
    AnkiConnect cardInfo fields:
    - queue: -3..2 (new=0, learning=1, review=2, suspended=-1, buried=-2/-3, etc.)
    - type: 0=new, 1=learning, 2=review, 3=relearning (varies by version)
    """
    if queue is None and typ is None:
        return "unknown"

    # Treat as NEW if either indicates new
    if queue == 0 or typ == 0:
        return "new"

    if queue in (1, 3) or typ in (1, 3):
        return "learning"

    if queue == 2 or typ == 2:
        return "review"

    if queue is not None and queue < 0:
        return "inactive"

    return "unknown"


def pick_best_status(statuses: list[str]) -> str:
    # If a note has multiple cards (e.g., forward+reverse), pick the “most learned” status.
    priority = {"review": 3, "learning": 2, "new": 1, "inactive": 0, "unknown": 0}
    best = "unknown"
    best_p = -1
    for s in statuses:
        p = priority.get(s, 0)
        if p > best_p:
            best, best_p = s, p
    return best


def main() -> None:
    # 1) All cards in the deck
    card_ids: list[int] = anki_request("findCards", {"query": f'deck:"{DECK_NAME}"'})
    print(f"Found {len(card_ids)} cards in deck: {DECK_NAME}")

    progress: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "deck": DECK_NAME,
        "term_field": TERM_FIELD,
        "terms": {},
    }

    # 2) Pull card info in batches
    BATCH = 500
    term_to_records: dict[str, list[dict[str, Any]]] = {}

    for i in range(0, len(card_ids), BATCH):
        batch_ids = card_ids[i : i + BATCH]
        infos: list[dict[str, Any]] = anki_request("cardsInfo", {"cards": batch_ids})
        print(f"  cardsInfo {i+1}-{min(i+len(batch_ids), len(card_ids))}/{len(card_ids)}")

        for ci in infos:
            fields = ci.get("fields") or {}
            raw_term = ""
            if TERM_FIELD in fields and isinstance(fields[TERM_FIELD], dict):
                raw_term = fields[TERM_FIELD].get("value") or ""
            else:
                # fallback: first field
                for _, v in fields.items():
                    if isinstance(v, dict) and v.get("value"):
                        raw_term = v["value"]
                        break

            term = normalize_term(raw_term)
            if not term or not is_cjk(term):
                continue

            queue = ci.get("queue")
            typ = ci.get("type")
            status = status_from_queue_type(queue, typ)

            rec = {
                "status": status,
                "learned": status != "new",
                "note_id": ci.get("note"),
                "card_id": ci.get("cardId") or ci.get("card_id") or ci.get("id"),
                "due": ci.get("due"),
                "ivl": ci.get("ivl"),  # interval (days) for review cards
                "queue": queue,
                "type": typ,
            }
            term_to_records.setdefault(term, []).append(rec)

    # 3) Collapse multiple cards per term into a single summary
    learned_count = 0
    for term, recs in term_to_records.items():
        statuses = [r["status"] for r in recs]
        best = pick_best_status(statuses)
        learned = best != "new"
        if learned:
            learned_count += 1

        # pick a representative record matching the best status (if possible)
        rep = next((r for r in recs if r["status"] == best), recs[0])
        progress["terms"][term] = {
            "status": best,
            "learned": learned,
            "note_id": rep.get("note_id"),
            "card_id": rep.get("card_id"),
            "due": rep.get("due"),
            "ivl": rep.get("ivl"),
        }

    print(f"Terms found: {len(progress['terms'])}")
    print(f"Learned (non-new): {learned_count}")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote: {OUTPUT_PATH.resolve()}")


if __name__ == "__main__":
    main()