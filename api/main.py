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
DICT_PATH = DATA_DIR / "master_dict.json"
PROGRESS_PATH = DATA_DIR / "anki_progress.json"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def load_books() -> List[Book]:
    if not BOOKS_PATH.exists():
        return []
    raw = json.loads(BOOKS_PATH.read_text(encoding="utf-8"))
    return [Book(**b) for b in raw]

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

@app.get("/books")
def list_books():
    books = load_books()
    return [{"id": b.id, "title": b.title} for b in books]

@app.get("/books/{book_id}")
def get_book(book_id: str):
    b = find_book_chapters(book_id)
    return {
        "schema_version": b.schema_version,
        "id": b.id,
        "title": b.title,
        "chapters": [{"id": ch.id, "title": ch.title} for ch in b.chapters],
    }

@app.get("/books/{book_id}/chapters/{chapter_id}")
def get_chapter(book_id: str, chapter_id: str):
    book, idx = find_book_and_chapter(book_id, chapter_id)
    ch = book.chapters[idx]

    d = {
        "book_id": book_id,
        "book_title": book.title,
        "chapter_id": ch.id,
        "chapter_title": ch.title,
        "text": ch.text,
    }

    # keep compatibility if you later add en_sentences to your Book model
    if hasattr(ch, "en_sentences"):
        d["en_sentences"] = getattr(ch, "en_sentences")

    return d

@app.get("/lookup/by_offset", response_model=LookupResult)
def lookup_by_offset(
    book_id: str = Query(...),
    chapter_id: str = Query(...),
    offset: int = Query(..., description="0-based character index in chapter text"),
):
    d = load_dict()
    book, idx = find_book_and_chapter(book_id, chapter_id)
    ch = book.chapters[idx]

    # Find the longest matching headword around the offset.
    text = ch.text
    if offset < 0 or offset >= len(text):
        raise HTTPException(400, detail="offset out of range")

    best = None
    best_span = None

    for start in range(max(0, offset - 12), offset + 1):
        for end in range(offset + 1, min(len(text), offset + 13) + 1):
            candidate = text[start:end]
            if candidate in d:
                if best is None or (end - start) > (best_span.end - best_span.start):
                    best = candidate
                    best_span = Span(text=candidate, start=start, end=end)

    if best is None:
        # fallback to single char
        best = text[offset]
        best_span = Span(text=best, start=offset, end=offset + 1)

    entry = d.get(best)
    if entry and isinstance(entry, dict):
        # Only pinyin/definitions are used by the typed response model here
        return LookupResult(
            selected=best_span,
            entry=DictionaryEntry(headword=best, pinyin=entry.get("pinyin", []), definitions=entry.get("definitions", [])),
        )

    return LookupResult(selected=best_span, entry=None)

@app.get("/lookup/in_text", response_model=LookupResult)
def lookup_in_text(
    text: str = Query(...),
    offset: int = Query(..., description="0-based character index in provided text"),
):
    d = load_dict()
    if offset < 0 or offset >= len(text):
        raise HTTPException(400, detail="offset out of range")

    best = None
    best_span = None

    for start in range(max(0, offset - 12), offset + 1):
        for end in range(offset + 1, min(len(text), offset + 13) + 1):
            candidate = text[start:end]
            if candidate in d:
                if best is None or (end - start) > (best_span.end - best_span.start):
                    best = candidate
                    best_span = Span(text=candidate, start=start, end=end)

    if best is None:
        best = text[offset]
        best_span = Span(text=best, start=offset, end=offset + 1)

    entry = d.get(best)
    if entry and isinstance(entry, dict):
        return LookupResult(
            selected=best_span,
            entry=DictionaryEntry(headword=best, pinyin=entry.get("pinyin", []), definitions=entry.get("definitions", [])),
        )

    return LookupResult(selected=best_span, entry=None)

@app.post("/import_book")
def import_book(book: Book):
    books = load_books()
    books = [b for b in books if b.id != book.id] + [book]
    save_books(books)
    return book

@app.post("/dict/put")
def dict_put(entry: DictionaryEntry):
    d = load_dict()
    existing = d.get(entry.headword, {}) if isinstance(d, dict) else {}
    tags = existing.get("tags") if isinstance(existing, dict) else None

    rec = {"pinyin": entry.pinyin, "definitions": entry.definitions}
    if tags:
        rec["tags"] = tags

    d[entry.headword] = rec
    save_dict(d)
    return {"ok": True, "headword": entry.headword}

@app.get("/dict")
def get_dict():
    """Return raw master_dict.json (including tags)."""
    return load_dict()

@app.get("/progress")
def get_progress():
    """Return anki_progress.json if present; otherwise empty."""
    if not PROGRESS_PATH.exists():
        return {"schema_version": 1, "terms": {}}
    return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))

PROGRESS_PATH = DATA_DIR / "anki_progress.json"

# Ensure your models import includes Span:
# from models import Book, LookupResult, DictionaryEntry, Span, DictJson

@app.get("/lookup/in_text", response_model=LookupResult)
def lookup_in_text(
    text: str = Query(...),
    offset: int = Query(..., description="0-based character index in provided text"),
):
    d = load_dict()
    if offset < 0 or offset >= len(text):
        raise HTTPException(400, detail="offset out of range")

    best = None
    best_span = None

    for start in range(max(0, offset - 12), offset + 1):
        for end in range(offset + 1, min(len(text), offset + 13) + 1):
            candidate = text[start:end]
            if candidate in d:
                if best is None or (end - start) > (best_span.end - best_span.start):
                    best = candidate
                    best_span = Span(text=candidate, start=start, end=end)

    if best is None:
        best = text[offset]
        best_span = Span(text=best, start=offset, end=offset + 1)

    entry = d.get(best)
    if entry and isinstance(entry, dict):
        return LookupResult(
            selected=best_span,
            entry=DictionaryEntry(
                headword=best,
                pinyin=entry.get("pinyin", []),
                definitions=entry.get("definitions", []),
            ),
        )

    return LookupResult(selected=best_span, entry=None)

@app.get("/dict")
def get_dict():
    """Return raw master_dict.json (including tags)."""
    return load_dict()

@app.get("/progress")
def get_progress():
    """Return anki_progress.json if present; otherwise empty."""
    if not PROGRESS_PATH.exists():
        return {"schema_version": 1, "terms": {}}
    return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
