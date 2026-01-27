from __future__ import annotations

import json
import re
import urllib.request
import shutil
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jieba
from pypinyin import lazy_pinyin, Style

from temp import OPENAI_API_KEY

ROOT = Path(__file__).resolve().parents[1]
INCOMING = ROOT / "data" / "books" / "incoming"
ARCHIVE = ROOT / "data" / "books" / "archive"
BOOKS_JSON = ROOT / "data" / "books.json"
DICT_JSON = ROOT / "data" / "master_dict.json"

# --- Translation backends ---
# Local (Ollama)
OLLAMA_URL = "http://192.168.1.153:11434/api/generate"
OLLAMA_MODEL = "mistral-nemo:latest"

# OpenAI
OPENAI_MODEL = "gpt-5-nano-2025-08-07"

# Choose backend: "local" or "openai"
TRANSLATION_BACKEND = "openai"

# --- Behavior flags ---
DO_TRANSLATE = True
FORCE_TRANSLATE = False
FILL_MISSING_VOCAB = True

# Batch size for OpenAI vocab-definition calls (larger = fewer requests)
VOCAB_DEFINITION_BATCH_SIZE = 50

# Only one output location: processed == archive (automatic move)
AUTO_ARCHIVE_INCOMING = True


def normalize_chapter_id_from_filename(path: Path) -> str:
    """Use filename stem as chapter id (e.g., ch1.txt -> ch1)."""
    stem = path.stem.strip()
    # enforce a simple pattern: starts with 'ch' + digits; otherwise slugify
    m = re.match(r"^(ch\d+)$", stem, flags=re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return slugify_id(stem)


def chapter_number_from_id(chapter_id: str) -> int:
    m = re.match(r"^ch(\d+)$", chapter_id, flags=re.IGNORECASE)
    if not m:
        return 0
    try:
        return int(m.group(1))
    except Exception:
        return 0



# --- OpenAI helpers ---

def openai_chat(prompt: str, timeout_s: int = 120) -> str:
    """Call OpenAI Responses API with a single user prompt. Returns plain text."""
    api_key = OPENAI_API_KEY or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set (set variable or environment).")

    url = "https://api.openai.com/v1/responses"
    payload = {
        "model": OPENAI_MODEL,
        "input": prompt,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"OpenAI request failed: {e}")

    data = json.loads(raw)

    # Extract text from the first output item
    try:
        for item in data.get("output", []):
            if item.get("type") == "message":
                parts = item.get("content", [])
                texts = [p.get("text", "") for p in parts if p.get("type") == "output_text"]
                out = "".join(texts).strip()
                if out:
                    return out
    except Exception:
        pass

    # Fallback: try top-level convenience fields if present
    out = (data.get("output_text") or "").strip()
    return out


# --- Batched OpenAI vocab definition helper ---
def openai_define_headwords_batch(headwords: list[str], timeout_s: int = 180) -> dict[str, list[str]]:
    """Define many headwords in one OpenAI call. Returns mapping headword -> definitions list."""
    # Keep it compact to minimize prompt tokens.
    headwords = [h for h in headwords if h]
    if not headwords:
        return {}

    prompt = (
        "You are a concise Chinese-English dictionary. "
        "For each Chinese headword provided, return ONLY valid JSON mapping each headword to an object with a 'definitions' array. "
        "Definitions must be short everyday English, 1-3 items. Do not include pinyin. "
        "Return exactly these keys (no extra).\n\n"
        "Headwords:\n" + "\n".join(headwords)
    )

    out = openai_chat(prompt, timeout_s=timeout_s)

    try:
        obj = json.loads(out)
    except Exception:
        # If the model returns non-JSON, fail closed.
        return {}

    results: dict[str, list[str]] = {}
    for hw in headwords:
        rec = obj.get(hw)
        if isinstance(rec, dict):
            defs = rec.get("definitions")
            if isinstance(defs, list):
                cleaned = [re.sub(r"\s+", " ", str(d)).strip() for d in defs if str(d).strip()]
                results[hw] = cleaned[:3]
    return results


def translate_zh_to_en(text: str, timeout_s: int = 120) -> str:
    """Dispatch translation to the selected backend."""
    if TRANSLATION_BACKEND == "openai":
        prompt = (
            "You are a professional translator. Translate the following Chinese text into natural English. "
            "Output only the translation.\n\n" + text
        )
        return openai_chat(prompt, timeout_s=timeout_s)

    # default: local (Ollama)
    return ollama_translate_zh_to_en_local(text, timeout_s=timeout_s)


def ollama_translate_zh_to_en_local(text: str, timeout_s: int = 120) -> str:
    """Translate Chinese -> English via local Ollama. Returns plain English text."""
    prompt = (
        "You are a professional translator. Translate the following Chinese text into natural English. "
        "Output only the translation.\n\n" + text
    )
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }

    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8")

    data = json.loads(raw)
    # Ollama returns {"response": "..."}
    out = (data.get("response") or "").strip()
    # normalize whitespace
    out = re.sub(r"\s+", " ", out).strip()
    return out


def pinyin_for(headword: str) -> str:
    """Return tone-mark pinyin for a Chinese headword, space-separated."""
    # Use tone marks (Style.TONE) which matches your existing dict style like "nándào"
    parts = lazy_pinyin(headword, style=Style.TONE, neutral_tone_with_five=False)
    return " ".join(parts)



def define_headword(headword: str, timeout_s: int = 120) -> list[str]:
    """Dispatch headword definition to the selected backend."""
    if TRANSLATION_BACKEND == "openai":
        prompt = (
            "You are a concise Chinese-English dictionary. "
            "Given a Chinese word or phrase, return ONLY valid JSON with this shape: "
            '{"definitions": ["..."]}. '
            "Definitions should be short, everyday English, 1-3 items. "
            "Do not include pinyin.\n\n"
            f"Headword: {headword}"
        )
        out = openai_chat(prompt, timeout_s=timeout_s)
        # Parse JSON from model
        try:
            obj = json.loads(out)
            defs = obj.get("definitions")
            if isinstance(defs, list):
                cleaned = [re.sub(r"\s+", " ", str(d)).strip() for d in defs if str(d).strip()]
                return cleaned[:3]
        except Exception:
            pass
        fallback = re.sub(r"\s+", " ", out).strip()
        return [fallback] if fallback else []

    # default: local (Ollama)
    return ollama_define_headword_local(headword, timeout_s=timeout_s)


def ollama_define_headword_local(headword: str, timeout_s: int = 120) -> list[str]:
    """Ask local Ollama for brief English definitions. Returns a list of definition strings."""
    prompt = (
        "You are a concise Chinese-English dictionary. "
        "Given a Chinese word or phrase, return ONLY valid JSON with this shape: "
        '{"definitions": ["..."]}. '
        "Definitions should be short, everyday English, 1-3 items. "
        "Do not include pinyin.\n\n"
        f"Headword: {headword}"
    )
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }

    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8")

    data = json.loads(raw)
    out = (data.get("response") or "").strip()

    # Try strict JSON parse first
    try:
        obj = json.loads(out)
        defs = obj.get("definitions")
        if isinstance(defs, list):
            cleaned = [re.sub(r"\s+", " ", str(d)).strip() for d in defs if str(d).strip()]
            return cleaned[:3]
    except Exception:
        pass

    # Fallback: treat as a single definition line
    fallback = re.sub(r"\s+", " ", out).strip()
    return [fallback] if fallback else []


def split_sentences_zh(text: str) -> list[tuple[int, int, str]]:
    """Split text into sentence spans using 。？！ (and ASCII ?!) keeping punctuation. Returns (start,end,sentence)."""
    if not text:
        return []

    end_punct = set("。？！?!")
    spans: list[tuple[int, int, str]] = []

    start = 0
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]
        if ch in end_punct:
            end = i + 1
            sent = text[start:end]
            # Skip whitespace-only segments
            if sent.strip():
                spans.append((start, end, sent))
            start = end
        i += 1

    # trailing fragment
    if start < n:
        sent = text[start:n]
        if sent.strip():
            spans.append((start, n, sent))

    return spans


def build_en_sentences(chapter_text: str, do_translate: bool) -> list[dict[str, Any]]:
    """Return list of {start,end,en} for the chapter text."""
    spans = split_sentences_zh(chapter_text)
    out: list[dict[str, Any]] = []

    total = len(spans)
    for idx, (start, end, zh) in enumerate(spans, start=1):
        # Keep original spacing/punctuation in offsets; translate trimmed sentence text.
        zh_clean = zh.strip()
        if not zh_clean:
            continue

        en = ""
        if do_translate:
            # light progress output (every 5 sentences and the last)
            if idx % 5 == 0 or idx == total:
                print(f"  Translating sentence {idx}/{total}...")
            en = translate_zh_to_en(zh_clean)

        out.append({"start": start, "end": end, "en": en})

    return out


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def slugify_id(s: str) -> str:
    # MVP stable-ish id: lowercase alnum + underscores
    out = []
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("_")
    return "".join(out).strip("_") or "book"


@dataclass
class ParsedChapter:
    title: str
    text: str


def parse_chapter_txt(path: Path) -> ParsedChapter:
    """Parse a chapter text file.

    - Title: first non-empty line
    - Body: remaining lines (preserving paragraph breaks)
    """
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip("\n")
    lines = raw.split("\n")

    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip():
            title = line.strip()
            body_start = i + 1
            break

    body = "\n".join(lines[body_start:]).strip("\n")
    if not title:
        # fallback if file is empty
        title = path.stem.strip() or path.name

    return ParsedChapter(title=title, text=body)


def upsert_book(books: list[dict[str, Any]], book_title: str) -> dict[str, Any]:
    book_id = slugify_id(book_title)
    for b in books:
        if b.get("id") == book_id:
            b["title"] = book_title
            b.setdefault("schema_version", 1)
            b.setdefault("chapters", [])
            return b

    b = {"schema_version": 1, "id": book_id, "title": book_title, "chapters": []}
    books.append(b)
    return b


def upsert_chapter(
    book: dict[str, Any],
    chapter_id: str,
    chapter_title: str,
    chapter_text: str,
    en_sentences: list[dict[str, Any]] | None = None,
) -> None:
    chapters: list[dict[str, Any]] = book.setdefault("chapters", [])
    for ch in chapters:
        if ch.get("id") == chapter_id:
            ch["title"] = chapter_title
            ch["text"] = chapter_text
            if en_sentences is not None:
                ch["en_sentences"] = en_sentences
            return

    rec: dict[str, Any] = {"id": chapter_id, "title": chapter_title, "text": chapter_text}
    if en_sentences is not None:
        rec["en_sentences"] = en_sentences
    chapters.append(rec)


# --- Missing vocab helpers ---

def is_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def clean_token(token: str) -> str:
    token = token.strip()
    token = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", token)
    return token


def compute_and_apply_missing_vocab(
    books: list[dict[str, Any]],
    vocab: dict[str, Any],
    fill_missing: bool,
) -> tuple[int, int, int, int]:
    """Returns (total_tokens_found, already_defined, missing_added, missing_filled)."""
    existing_keys = set(vocab.keys())

    all_text: list[str] = []
    for book in books:
        bt = book.get("title", "")
        if bt:
            all_text.append(bt)
        for chapter in book.get("chapters", []):
            ct = chapter.get("title", "")
            if ct:
                all_text.append(ct)
            tx = chapter.get("text", "")
            if tx:
                all_text.append(tx)

    full_text = "\n".join(all_text)

    # words
    word_tokens = set(jieba.cut(full_text, cut_all=False))
    word_tokens = {clean_token(w) for w in word_tokens if w.strip()}

    # chars
    char_tokens = {ch for ch in full_text if "\u4e00" <= ch <= "\u9fff"}

    all_tokens = word_tokens.union(char_tokens)

    missing = sorted(
        t for t in all_tokens
        if t and t not in existing_keys and is_cjk(t)
    )

    filled = 0

    # If not filling, just add null entries.
    if not fill_missing:
        for t in missing:
            vocab[t] = None
        return (len(all_tokens), len(existing_keys), len(missing), filled)

    # Filling mode.
    if TRANSLATION_BACKEND == "openai":
        total_missing = len(missing)
        for i in range(0, total_missing, VOCAB_DEFINITION_BATCH_SIZE):
            batch = missing[i:i + VOCAB_DEFINITION_BATCH_SIZE]
            print(f"Defining vocab batch {i + 1}-{min(i + len(batch), total_missing)}/{total_missing}...")

            defs_map = openai_define_headwords_batch(batch)

            for t in batch:
                py = pinyin_for(t)
                defs = defs_map.get(t)
                if not defs:
                    # fallback to single call if batch failed for this item
                    defs = define_headword(t)
                vocab[t] = {
                    "pinyin": [py] if py else [],
                    "definitions": defs,
                }
                filled += 1

        return (len(all_tokens), len(existing_keys), len(missing), filled)

    # Local backend: fill one-by-one (Ollama)
    total_missing = len(missing)
    for idx, t in enumerate(missing, start=1):
        if idx % 25 == 0 or idx == total_missing:
            print(f"Defining vocab {idx}/{total_missing}...")
        py = pinyin_for(t)
        defs = define_headword(t)
        vocab[t] = {
            "pinyin": [py] if py else [],
            "definitions": defs,
        }
        filled += 1

    return (len(all_tokens), len(existing_keys), len(missing), filled)


def write_processed_chapter(book_title: str, chapter_id: str, parsed: ParsedChapter) -> None:
    out_dir = ARCHIVE / book_title
    out_dir.mkdir(parents=True, exist_ok=True)

    # Standardized processed filename: <chapter_id>.txt (e.g., ch2.txt)
    out_path = out_dir / f"{chapter_id}.txt"

    # Store title + blank line + body (keeps your paragraph structure)
    content = parsed.title.strip()
    if parsed.text.strip():
        content += "\n\n" + parsed.text.strip()
    content += "\n"

    out_path.write_text(content, encoding="utf-8")


def archive_incoming_file(book_title: str, src_path: Path) -> None:
    """Move an incoming source file into the archive folder to avoid re-processing on reruns."""
    dest_dir = ARCHIVE / book_title
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_path = dest_dir / src_path.name

    # If a file with the same name already exists in the archive, append a numeric suffix.
    if dest_path.exists():
        stem = src_path.stem
        suffix = src_path.suffix
        k = 1
        while True:
            candidate = dest_dir / f"{stem}_{k}{suffix}"
            if not candidate.exists():
                dest_path = candidate
                break
            k += 1

    shutil.move(str(src_path), str(dest_path))


def main() -> None:
    books: list[dict[str, Any]] = load_json(BOOKS_JSON, default=[])
    vocab: dict[str, Any] = load_json(DICT_JSON, default={})

    if not INCOMING.exists():
        raise SystemExit(f"Missing folder: {INCOMING}")

    # book folder = title
    for book_dir in sorted(p for p in INCOMING.iterdir() if p.is_dir()):
        book_title = book_dir.name
        book = upsert_book(books, book_title)

        # deterministic order: sort by chapter number from filename if possible
        txt_files = sorted(
            book_dir.glob("*.txt"),
            key=lambda p: (chapter_number_from_id(normalize_chapter_id_from_filename(p)), p.name.lower()),
        )

        for txt in txt_files:
            parsed = parse_chapter_txt(txt)

            # Use filename as chapter id (e.g., ch2.txt -> ch2)
            chapter_id = normalize_chapter_id_from_filename(txt)

            # Build sentence-level translations (offsets are into parsed.text)
            existing_ch = next((c for c in book.get("chapters", []) if c.get("id") == chapter_id), None)
            has_existing_en = bool(existing_ch and existing_ch.get("en_sentences"))

            en_sentences = None
            if DO_TRANSLATE and (FORCE_TRANSLATE or not has_existing_en):
                en_sentences = build_en_sentences(parsed.text, do_translate=True)

            upsert_chapter(book, chapter_id, parsed.title, parsed.text, en_sentences=en_sentences)
            write_processed_chapter(book_title, chapter_id, parsed)
            if AUTO_ARCHIVE_INCOMING:
                archive_incoming_file(book_title, txt)

    # Save books.json
    save_json(BOOKS_JSON, books)

    # Update master_dict.json with missing vocab
    total_tokens, already_defined, missing_added, missing_filled = compute_and_apply_missing_vocab(
        books,
        vocab,
        fill_missing=FILL_MISSING_VOCAB,
    )
    save_json(DICT_JSON, vocab)

    print(f"Translation backend: {TRANSLATION_BACKEND}")
    print(f"Updated {BOOKS_JSON} from {INCOMING}")
    print(f"Chapters written and archived under: {ARCHIVE}")
    if DO_TRANSLATE:
        if TRANSLATION_BACKEND == "openai":
            print(f"Sentence translations written to books.json using OpenAI model: {OPENAI_MODEL}")
        else:
            print(f"Sentence translations written to books.json using Ollama model: {OLLAMA_MODEL}")
    print(f"Total tokens found: {total_tokens}")
    print(f"Already defined: {already_defined}")
    print(f"Missing entries added to master_dict.json: {missing_added}")
    if FILL_MISSING_VOCAB:
        print(f"Missing entries filled (pinyin/definitions): {missing_filled}")


if __name__ == "__main__":
    main()