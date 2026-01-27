from __future__ import annotations

from typing import Optional, List, Dict
from pydantic import BaseModel

class EnSentence(BaseModel):
    start: int
    end: int
    en: str

class Chapter(BaseModel):
    id: str
    title: str
    text: str
    en_sentences: Optional[List[EnSentence]] = None


class Book(BaseModel):
    schema_version: int = 1
    id: str
    title: str
    chapters: List[Chapter]


class DictionaryEntry(BaseModel):
    headword: str
    pinyin: List[str]
    definitions: List[str]


class Span(BaseModel):
    text: str
    start: int  # inclusive char offset
    end: int    # exclusive char offset


class LookupResult(BaseModel):
    selected: Span
    entry: Optional[DictionaryEntry] = None


# master_dict.json format (MVP): { "今天": {"pinyin":["jīntiān"], "definitions":["today"]}, ... }
DictJson = Dict[str, Dict[str, List[str]]]