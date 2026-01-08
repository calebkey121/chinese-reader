from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from models import Book, LookupResult, DictionaryEntry, Span, DictJson

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
BOOKS_PATH = DATA_DIR / "books.json"
DICT_PATH = DATA_DIR / "dict.json"

app = FastAPI(title="Graded Reader MVP", version="0.1.0")

# For local dev. Tighten later.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def load_books() -> List[Book]:
    if not BOOKS_PATH.exists():
        return []
    raw = json.loads(BOOKS_PATH.read_text(encoding="utf-8"))
    return [Book.model_validate(b) for b in raw]

def save_books(books: List[Book]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BOOKS_PATH.write_text(
        json.dumps([b.model_dump() for b in books], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def load_dict() -> DictJson:
    if not DICT_PATH.exists():
        return {}
    return json.loads(DICT_PATH.read_text(encoding="utf-8"))

def save_dict(d: DictJson) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DICT_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

def find_book_and_chapter(book_id: str, chapter_id: str) -> tuple[Book, int]:
    books = load_books()
    for b in books:
        if b.id == book_id:
            for i, ch in enumerate(b.chapters):
                if ch.id == chapter_id:
                    return b, i
            raise HTTPException(404, detail=f"chapter_id not found: {chapter_id}")
    raise HTTPException(404, detail=f"book_id not found: {book_id}")

def find_book_chapters(book_id: str):
    books = load_books()
    for b in books:
        if b.id == book_id:
            return b
    raise HTTPException(404, detail=f"book_id not found: {book_id}")

def lookup_entry(headword: str, d: DictJson) -> Optional[DictionaryEntry]:
    if headword not in d:
        return None
    payload = d[headword]
    return DictionaryEntry(
        headword=headword,
        pinyin=payload.get("pinyin", []),
        definitions=payload.get("definitions", []),
    )

def clamp_offset(offset: int, text_len: int) -> int:
    if text_len <= 0:
        return 0
    if offset < 0:
        return 0
    if offset >= text_len:
        return text_len - 1
    return offset

def select_span_by_offset(text: str, offset: int, d: DictJson) -> Span:
    """
    Longest-match selection (MVP):
    - Find the longest dictionary entry (up to max_len) whose span includes `offset`.
    - If none found, fall back to single character at offset.
    """
    n = len(text)
    if n == 0:
        return Span(text="", start=0, end=0)

    o = clamp_offset(offset, n)

    max_len = 4  # set to 4; you can bump to 6 later if you want

    best = None  # (length, start, end, word)

    # Consider spans that include the tapped offset.
    # Start can be at most `o` (so it includes o), and at least `o-(max_len-1)`.
    start_min = max(0, o - (max_len - 1))
    start_max = o

    for start in range(start_min, start_max + 1):
        # longest-first for this start
        for length in range(max_len, 0, -1):
            end = start + length
            if end > n:
                continue
            if not (start <= o < end):
                continue

            w = text[start:end]
            if w in d:
                # prefer longer; if tie, prefer the one with start closest to o (optional)
                cand = (length, start, end, w)
                if best is None:
                    best = cand
                else:
                    if cand[0] > best[0]:
                        best = cand
                    elif cand[0] == best[0]:
                        # tie-break: prefer spans starting closer to tapped offset
                        if abs(cand[1] - o) < abs(best[1] - o):
                            best = cand
                break  # don't check shorter lengths for this start

    if best is not None:
        _, start, end, w = best
        return Span(text=w, start=start, end=end)

    # fallback: single character
    w1 = text[o:o + 1]
    return Span(text=w1, start=o, end=o + 1)

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/books")
def list_books():
    books = load_books()
    return [{"id": b.id, "title": b.title} for b in books]

@app.get("/books/{book_id}")
def get_book(book_id: str):
    book = find_book_chapters(book_id)
    return {
        "book_id": book.id,
        "title": book.title,
        "chapters": [{"id": c.id, "title": c.title} for c in book.chapters],
    }

@app.get("/books/{book_id}/chapters/{chapter_id}")
def get_book_chapter(book_id: str, chapter_id: str):
    book, idx = find_book_and_chapter(book_id, chapter_id)
    ch = book.chapters[idx]
    return {
        "book_id": book.id,
        "book_title": book.title,
        "chapter_id": ch.id,
        "chapter_title": ch.title,
        "text": ch.text,
        "en_sentences": getattr(ch, "en_sentences", []),
    }

@app.get("/lookup/by_offset", response_model=LookupResult)
def lookup_by_offset(
    book_id: str = Query(...),
    chapter_id: str = Query(...),
    offset: int = Query(..., description="0-based character index in chapter text"),
):
    d = load_dict()
    book, idx = find_book_and_chapter(book_id, chapter_id)
    ch = book.chapters[idx]

    span = select_span_by_offset(ch.text, offset, d)
    entry = lookup_entry(span.text, d)
    return LookupResult(selected=span, entry=entry)

@app.get("/lookup/in_text", response_model=LookupResult)
def lookup_in_text(
    text: str = Query(..., description="Arbitrary text to lookup within"),
    offset: int = Query(..., description="0-based character index into `text`"),
):
    d = load_dict()
    span = select_span_by_offset(text, offset, d)
    entry = lookup_entry(span.text, d)
    return LookupResult(selected=span, entry=entry)

# Optional: quick way to add/import a book (MVP convenience)
@app.post("/books/import", response_model=Book)
def import_book(book: Book):
    books = load_books()
    # overwrite if same id
    books = [b for b in books if b.id != book.id] + [book]
    save_books(books)
    return book

# Optional: add/patch dictionary entries (MVP convenience)
@app.post("/dict/put")
def dict_put(entry: DictionaryEntry):
    d = load_dict()
    d[entry.headword] = {"pinyin": entry.pinyin, "definitions": entry.definitions}
    save_dict(d)
    return {"ok": True, "headword": entry.headword}
