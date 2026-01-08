const API = "http://192.168.1.217:8000";

const bookSelect = document.getElementById("bookSelect");
const chapterSelect = document.getElementById("chapterSelect");
const titleEl = document.getElementById("title");
const readerEl = document.getElementById("reader");

const popup = document.getElementById("popup");
const popupWord = document.getElementById("popupWord");
const popupPinyin = document.getElementById("popupPinyin");
const popupDefs = document.getElementById("popupDefs");
const popupDebug = document.getElementById("popupDebug");
const playAllBtn = document.getElementById("playAllBtn");
const ttsRateEl = document.getElementById("ttsRate");
const ttsRateLabel = document.getElementById("ttsRateLabel");

let isPlayingAll = false;
let current = { bookId: null, chapterId: null, text: "", titleText: "", enSentences: [] };


function getTtsRate() {
  const v = Number(ttsRateEl?.value ?? 1.0);
  return Number.isFinite(v) ? v : 1.0;
}

if (ttsRateEl && ttsRateLabel) {
  ttsRateLabel.textContent = `${getTtsRate().toFixed(2)}×`;
  ttsRateEl.addEventListener("input", () => {
    ttsRateLabel.textContent = `${getTtsRate().toFixed(2)}×`;
  });
}

function speak(text) {
  if (!text) return;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "zh-CN";
  u.rate = getTtsRate();
  // cancel so rapid clicks don't overlap
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(u);
}

function speakAll(text) {
  if (!text) return;

  const u = new SpeechSynthesisUtterance(text);
  u.lang = "zh-CN";
  u.rate = getTtsRate();
  isPlayingAll = true;
  playAllBtn.textContent = "Pause";

  u.onend = () => {
    isPlayingAll = false;
    playAllBtn.textContent = "Play";
  };
  u.onerror = () => {
    isPlayingAll = false;
    playAllBtn.textContent = "Play";
  };

  window.speechSynthesis.cancel(); // stop any current speech
  window.speechSynthesis.speak(u);
}

function stopAll() {
  window.speechSynthesis.cancel();
  isPlayingAll = false;
  playAllBtn.textContent = "Play";
}

function showPopup(x, y) {
  popup.classList.remove("hidden");
  popup.style.left = `${x}px`;
  popup.style.top = `${y}px`;
}

function hidePopup() {
  popup.classList.add("hidden");
}

// Click-away to close the popup
document.addEventListener("click", (e) => {
  if (popup.classList.contains("hidden")) return;

  const target = e.target;
  if (!(target instanceof HTMLElement)) return;

  // If click is inside the popup, do nothing
  if (popup.contains(target)) return;

  // If click is on a character span, the reader handler will manage showing the popup
  if (target.classList.contains("ch")) return;

  hidePopup();
});

playAllBtn.addEventListener("click", () => {
  if (isPlayingAll) {
    stopAll();
    return;
  }

  // include title if you want it read too:
  const title = (current.titleText || "").trim();
  const body = (current.text || "").trim();
  const combined = title ? `${title}\n\n${body}` : body;

  speakAll(combined);
});

// Prevent clicks inside the popup from bubbling to the document handler
popup.addEventListener("click", (e) => {
  e.stopPropagation();
});

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}


async function loadBooks() {
  const books = await fetchJson(`${API}/books`);
  bookSelect.innerHTML = "";

  for (const b of books) {
    const opt = document.createElement("option");
    opt.value = b.id;
    opt.textContent = b.title;
    bookSelect.appendChild(opt);
  }

  if (books.length) {
    current.bookId = books[0].id;
    await loadChapters(current.bookId);
  } else {
    titleEl.textContent = "";
    readerEl.innerHTML = "";
    chapterSelect.innerHTML = "";
    current = { bookId: null, chapterId: null, text: "" };
    hidePopup();
  }
}

async function loadChapters(bookId) {
  const book = await fetchJson(`${API}/books/${bookId}`);

  chapterSelect.innerHTML = "";

  const chapters = Array.isArray(book.chapters) ? book.chapters : [];
  if (!chapters.length) {
    // Show book title if present, clear reader
    titleEl.textContent = book.title ? `${book.title}` : "";
    readerEl.innerHTML = "";
    current.chapterId = null;
    current.text = "";
    hidePopup();
    return;
  }

  for (const ch of chapters) {
    const opt = document.createElement("option");
    opt.value = ch.id;
    opt.textContent = ch.title || ch.id;
    chapterSelect.appendChild(opt);
  }

  // Default to the first chapter
  const firstId = chapters[0].id;
  current.bookId = bookId;
  current.chapterId = firstId;
  chapterSelect.value = firstId;
  hidePopup();
  await loadChapter(bookId, firstId);
}

async function loadChapter(bookId, chapterId) {
  const ch = await fetchJson(`${API}/books/${bookId}/chapters/${chapterId}`);
  
  const bookTitle = ch.book_title || "";
  const chapterTitle = ch.chapter_title || "";
  const fullTitle =
  bookTitle && chapterTitle ? `${bookTitle} — ${chapterTitle}` : (chapterTitle || bookTitle);
  
  current.text = ch.text || "";
  current.bookId = bookId;
  current.chapterId = chapterId;
  current.titleText = fullTitle;
  current.enSentences = Array.isArray(ch.en_sentences) ? ch.en_sentences : [];

  hidePopup();
  renderChapter(fullTitle, current.text);
}


function renderChapter(titleText, bodyText) {
  // Title offsets are local; your title click handler doesn't call the API anyway
  titleEl.innerHTML = "";
  renderCharsInto(titleEl, titleText, 0);

  // Paragraphs: offsets must match the ORIGINAL chapter text
  readerEl.innerHTML = "";

  const paragraphs = splitIntoParagraphsWithOffsets(bodyText);
  for (const pInfo of paragraphs) {
    const p = document.createElement("p");
    p.className = "paragraph";
    renderCharsInto(p, pInfo.text, pInfo.start);
    readerEl.appendChild(p);
  }
}

function splitIntoParagraphsWithOffsets(text) {
  const t = (text || "").replace(/\r\n/g, "\n");

  // Match each paragraph (anything up to a blank line), and keep its start index
  const re = /([^\n].*?)(?=\n\s*\n+|$)/gs;

  const out = [];
  for (const m of t.matchAll(re)) {
    const paragraphText = m[1];
    const start = m.index ?? 0;
    if (paragraphText.length) out.push({ text: paragraphText, start });
  }

  // If the chapter is empty or only blank lines
  if (!out.length) return [{ text: "", start: 0 }];
  return out;
}

function renderCharsInto(container, text, baseOffset = 0) {
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];

    // Keep offsets correct across internal newlines without making them clickable
    if (ch === "\n") {
      const br = document.createElement("br");
      br.dataset.offset = String(baseOffset + i);
      container.appendChild(br);
      continue;
    }

    const span = document.createElement("span");
    span.className = "ch";
    span.dataset.offset = String(baseOffset + i);
    span.textContent = ch;
    container.appendChild(span);
  }
}

function findEnSentenceByOffset(offset) {
  for (const s of current.enSentences) {
    if (typeof s.start === "number" && typeof s.end === "number" && s.start <= offset && offset < s.end) {
      return s;
    }
  }
  return null;
}

let pressTimer = null;
let longPressFired = false;

readerEl.addEventListener("pointerdown", (e) => {
  const target = e.target;
  if (!(target instanceof HTMLElement)) return;
  if (!target.classList.contains("ch")) return;

  const offset = Number(target.dataset.offset);
  if (!Number.isFinite(offset)) return;

  longPressFired = false;
  pressTimer = setTimeout(() => {
    longPressFired = true;

    const s = findEnSentenceByOffset(offset);
    if (!s) return;

    // Show translation in the existing popup
    popupWord.textContent = "EN";
    popupPinyin.textContent = "";
    popupDefs.textContent = s.en;
    popupDebug.textContent = `en_sentence [${s.start}, ${s.end})`;

    showPopup(e.clientX + 10, e.clientY + 10);
    // No speak() here unless you want English TTS too
  }, 450); // long-press threshold
});

readerEl.addEventListener("pointerup", () => {
  if (pressTimer) clearTimeout(pressTimer);
  pressTimer = null;
});

readerEl.addEventListener("pointercancel", () => {
  if (pressTimer) clearTimeout(pressTimer);
  pressTimer = null;
});

readerEl.addEventListener("click", async (e) => {
  if (!current.bookId || !current.chapterId) return;
  if (longPressFired) {
    longPressFired = false;
    return;
  }
  const target = e.target;
  if (!(target instanceof HTMLElement)) return;
  if (!target.classList.contains("ch")) return;

  const offset = Number(target.dataset.offset);
  if (!Number.isFinite(offset)) return;

  const url =
    `${API}/lookup/by_offset?book_id=${encodeURIComponent(current.bookId)}` +
    `&chapter_id=${encodeURIComponent(current.chapterId)}` +
    `&offset=${offset}`;

  const result = await fetchJson(url);
  const word = result?.selected?.text || target.textContent || "";

  // Update popup content
  popupWord.textContent = word;

  if (result.entry) {
    popupPinyin.textContent = (result.entry.pinyin || []).join(" / ");
    popupDefs.textContent = (result.entry.definitions || []).join("; ");
  } else {
    popupPinyin.textContent = "(no entry)";
    popupDefs.textContent = "";
  }

  popupDebug.textContent = `span [${result.selected.start}, ${result.selected.end}) offset=${offset}`;

  // Show popup near click and speak immediately
  showPopup(e.clientX + 10, e.clientY + 10);
  speak(word);
});

titleEl.addEventListener("click", async (e) => {
  if (!current.titleText) return;

  const target = e.target;
  if (!(target instanceof HTMLElement)) return;
  if (!target.classList.contains("ch")) return;

  const offset = Number(target.dataset.offset);
  if (!Number.isFinite(offset)) return;

  const url =
    `${API}/lookup/in_text?text=${encodeURIComponent(current.titleText)}` +
    `&offset=${offset}`;

  const result = await fetchJson(url);
  const word = result?.selected?.text || target.textContent || "";

  popupWord.textContent = word;

  if (result.entry) {
    popupPinyin.textContent = (result.entry.pinyin || []).join(" / ");
    popupDefs.textContent = (result.entry.definitions || []).join("; ");
  } else {
    popupPinyin.textContent = "(no entry)";
    popupDefs.textContent = "";
  }

  popupDebug.textContent = `title span [${result.selected.start}, ${result.selected.end}) offset=${offset}`;
  showPopup(e.clientX + 10, e.clientY + 10);
  speak(word);
});

bookSelect.addEventListener("change", async () => {
  hidePopup();
  await loadChapters(bookSelect.value);
});

chapterSelect.addEventListener("change", async () => {
  hidePopup();
  await loadChapter(bookSelect.value, chapterSelect.value);
});

loadBooks().catch((err) => {
  console.error(err);
  alert(String(err));
});