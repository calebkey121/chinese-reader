"""parse_story.py

Takes a simple LLM-generated story JSON (book zh_title + en_title + chapters with zh_title/en_title/text + en_sentences)
and merges it into master_books.json using schema_version=1.

Input (LLM JSON) example:
{
  "zh_title": "雨天的咖啡店",
  "en_title": "The Coffee Shop on a Rainy Day",
  "chapters": [
    {"zh_title": "窗边的位置", "en_title": "The Seat by the Window", "text": "...", "en_sentences": ["..."]}
  ]
}

Output (master_books.json) appends:
{
  "schema_version": 1,
  "id": "bookX",
  "zh_title": "...",
  "en_title": "...",
  "chapters": [
    {
      "id": "ch1",
      "zh_title": "第一章：...",
      "en_title": "...",
      "text": "...",
      "en_sentences": [{"start": 0, "end": 5, "en": "..."}, ...]
    }
  ]
}

Offsets (start/end) are character indexes into the chapter's full text string.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, List


# -----------------------------
# Helpers
# -----------------------------

_CN_DIGITS = {
    0: "零",
    1: "一",
    2: "二",
    3: "三",
    4: "四",
    5: "五",
    6: "六",
    7: "七",
    8: "八",
    9: "九",
}


def int_to_cn(n: int) -> str:
    """Convert 1..99 to Chinese numerals suitable for chapter numbering."""
    if n <= 0:
        raise ValueError("n must be >= 1")
    if n < 10:
        return _CN_DIGITS[n]
    if n == 10:
        return "十"
    tens = n // 10
    ones = n % 10
    if tens == 1:
        prefix = "十"
    else:
        prefix = _CN_DIGITS[tens] + "十"
    if ones == 0:
        return prefix
    return prefix + _CN_DIGITS[ones]


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def next_book_id(master_books: List[Dict[str, Any]]) -> str:
    """Return next id like book1, book2, ... based on existing master list."""
    max_n = 0
    for b in master_books:
        bid = str(b.get("id", ""))
        m = re.fullmatch(r"book(\d+)", bid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"book{max_n + 1}"


# -----------------------------
# Sentence span extraction
# -----------------------------

_SENT_END = set(["。", "！", "？", "!", "?"])


@dataclass
class Span:
    start: int
    end: int
    text: str


def chinese_sentence_spans(text: str) -> List[Span]:
    """Return sentence spans (start/end char offsets) in the original text.

    Splits primarily on Chinese sentence-ending punctuation: 。！？ plus ASCII !?

    Notes:
    - Keeps punctuation attached to the sentence.
    - Newlines are preserved in the original text; offsets refer to the original text.
    - If trailing text has no terminal punctuation, it becomes a final sentence.
    """
    spans: List[Span] = []
    start = 0
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]
        if ch in _SENT_END:
            end = i + 1
            seg = text[start:end]
            # Avoid emitting empty/whitespace-only segments
            if seg.strip():
                spans.append(Span(start=start, end=end, text=seg))
            start = end
        i += 1

    if start < n:
        tail = text[start:n]
        if tail.strip():
            spans.append(Span(start=start, end=n, text=tail))

    return spans


def align_en_sentences_to_spans(text: str, en_sentences: List[str]) -> List[Dict[str, Any]]:
    """Align each English sentence to a Chinese sentence span by index.

    This assumes the LLM produced en_sentences in the same order as the Chinese sentences.
    If counts mismatch, aligns up to the minimum and logs a warning via stderr.
    """
    spans = chinese_sentence_spans(text)

    out: List[Dict[str, Any]] = []
    m = min(len(spans), len(en_sentences))
    for idx in range(m):
        s = spans[idx]
        out.append({"start": s.start, "end": s.end, "en": en_sentences[idx]})

    return out


# -----------------------------
# New helper functions for zh/en title support
# -----------------------------

def get_title_zh(obj: Dict[str, Any]) -> str:
    """Prefer zh_title, fall back to title."""
    return str(obj.get("zh_title") or obj.get("title") or "").strip()


def get_title_en(obj: Dict[str, Any]) -> str:
    """Prefer en_title; allow missing."""
    return str(obj.get("en_title") or "").strip()


_CHAPTER_PREFIX_RE = re.compile(r"^第[零一二三四五六七八九十]+章：")


def base_chapter_title_zh(ch_zh_title: str) -> str:
    """Strip the generated prefix like 第三章： from a chapter zh_title."""
    return _CHAPTER_PREFIX_RE.sub("", (ch_zh_title or "").strip())


def find_book_index_by_zh_title(master_books: List[Dict[str, Any]], zh_title: str) -> int:
    """Return index of the book with matching zh_title, else -1."""
    target = (zh_title or "").strip()
    if not target:
        return -1
    for i, b in enumerate(master_books):
        if get_title_zh(b) == target:
            return i
    return -1


# -----------------------------
# Main transform
# -----------------------------


def normalize_llm_book(llm: Any) -> Dict[str, Any]:
    """Accept either a single book object or a 1-item list, return book dict."""
    if isinstance(llm, list):
        if not llm:
            raise ValueError("LLM JSON is an empty list")
        if len(llm) != 1:
            raise ValueError("LLM JSON list must contain exactly 1 book object")
        llm = llm[0]
    if not isinstance(llm, dict):
        raise ValueError("LLM JSON must be a dict (book object) or a 1-item list")
    if "chapters" not in llm:
        raise ValueError("LLM JSON must contain 'chapters'")
    if not get_title_zh(llm):
        raise ValueError("LLM JSON must contain a non-empty 'zh_title' (or fallback 'title')")
    if not isinstance(llm["chapters"], list):
        raise ValueError("LLM JSON 'chapters' must be a list")
    return llm


def build_chapters(
    llm_chapters: List[Dict[str, Any]],
    start_index: int,
    existing_base_titles: set[str],
) -> List[Dict[str, Any]]:
    chapters_out: List[Dict[str, Any]] = []

    next_i = start_index
    for ch in llm_chapters:
        if not isinstance(ch, dict):
            raise ValueError(f"Chapter input is not an object: {type(ch)}")

        raw_title_zh = get_title_zh(ch)
        raw_title_en = get_title_en(ch)
        if not raw_title_zh:
            raw_title_zh = f"第{next_i}章"

        # De-dup by base title (ignore 第X章： prefix on existing chapters)
        base_title = base_chapter_title_zh(raw_title_zh)
        if base_title in existing_base_titles:
            print(
                f"[skip] Chapter already exists by title: {base_title}",
                file=sys.stderr,
            )
            continue

        text = str(ch.get("text", ""))
        en_sents = ch.get("en_sentences", [])
        if not isinstance(en_sents, list) or any(not isinstance(x, str) for x in en_sents):
            raise ValueError("Chapter 'en_sentences' must be a list of strings")

        chapter_title_zh = f"第{int_to_cn(next_i)}章：{raw_title_zh}"
        aligned = align_en_sentences_to_spans(text, en_sents)

        chapters_out.append(
            {
                "id": f"ch{next_i}",
                "zh_title": chapter_title_zh,
                "en_title": raw_title_en,
                "text": text,
                "en_sentences": aligned,
            }
        )

        existing_base_titles.add(base_title)
        next_i += 1

    return chapters_out


def build_new_book(llm_book: Dict[str, Any], book_id: str) -> Dict[str, Any]:
    title_zh = get_title_zh(llm_book)
    title_en = get_title_en(llm_book)
    if not title_zh:
        raise ValueError("LLM book zh_title/title is empty")

    existing_base_titles: set[str] = set()
    chapters_out = build_chapters(
        llm_chapters=llm_book.get("chapters", []),
        start_index=1,
        existing_base_titles=existing_base_titles,
    )

    return {
        "schema_version": 1,
        "id": book_id,
        "zh_title": title_zh,
        "en_title": title_en,
        "chapters": chapters_out,
    }


def load_master_books(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    data = load_json(path)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    # Allow wrapping object in the future; for now be strict.
    raise ValueError("master_books.json must be a JSON array")


def main() -> None:
    ap = argparse.ArgumentParser(description="Parse LLM story JSON and merge into master_books.json")
    ap.add_argument(
        "--input",
        default=os.path.join(os.path.dirname(__file__), "example_story.json"),
        help="Path to LLM story JSON file (simple schema)",
    )
    ap.add_argument(
        "--master",
        default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "api", "data", "master_books.json")),
        help="Path to master_books.json (default: story_generation/master_books.json)",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="Optional output path; if omitted, writes back to --master",
    )

    args = ap.parse_args()

    llm_raw = load_json(args.input)
    llm_book = normalize_llm_book(llm_raw)

    master = load_master_books(args.master)

    book_zh = get_title_zh(llm_book)
    book_idx = find_book_index_by_zh_title(master, book_zh)

    if book_idx == -1:
        bid = next_book_id(master)
        new_book = build_new_book(llm_book, bid)
        master.append(new_book)
        print(f"Appended {bid} (new book) to {args.master}")
    else:
        # Append chapters into existing book
        existing_book = master[book_idx]
        existing_chapters = existing_book.get("chapters", [])
        if not isinstance(existing_chapters, list):
            raise ValueError("Existing book has invalid 'chapters' (must be a list)")

        # Build a set of existing base titles for de-dup
        existing_base_titles: set[str] = set()
        for ch in existing_chapters:
            if isinstance(ch, dict):
                existing_base_titles.add(base_chapter_title_zh(get_title_zh(ch)))

        start_index = len(existing_chapters) + 1
        appended = build_chapters(
            llm_chapters=llm_book.get("chapters", []),
            start_index=start_index,
            existing_base_titles=existing_base_titles,
        )

        if appended:
            existing_book.setdefault("schema_version", 1)
            existing_book.setdefault("zh_title", book_zh)
            # Only set en_title if missing; keep existing if present
            if "en_title" not in existing_book or not str(existing_book.get("en_title") or "").strip():
                existing_book["en_title"] = get_title_en(llm_book)

            existing_chapters.extend(appended)
            print(
                f"Appended {len(appended)} chapter(s) to existing book id={existing_book.get('id')}",
                file=sys.stderr,
            )
        else:
            print(
                f"No new chapters appended (all duplicates?) for book zh_title={book_zh}",
                file=sys.stderr,
            )

    out_path = args.out or args.master
    save_json(out_path, master)

    print(f"Wrote master_books to {out_path}")


if __name__ == "__main__":
    main()
