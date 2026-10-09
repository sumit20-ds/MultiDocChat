"""
Simple guardrails for the multi_doc_chat project.

Save this file as:  multi_doc_chat/utils/guardrails.py

It has no project imports on purpose (no logger, no config), so it is easy to
test and can never cause a circular import. Functions return a result and a
reason; the CALLER decides what to log or show.

Checkpoints covered:
  1. Uploads    -> limit_upload_count(), check_saved_file()
  2. Documents  -> filter_chunks()
  3. Questions  -> check_user_input(), rate_limiter
  6. Answers    -> sanitize_output()
  Everything for one question in a single call -> guarded_invoke()

These are first-layer defences. Pattern lists catch obvious attacks only.
"""
from __future__ import annotations

import re
import threading
import time
import unicodedata
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional, Tuple

# --------------------------------------------------------------------------
# Settings: change these numbers to fit your app
# --------------------------------------------------------------------------
MAX_QUESTION_CHARS = 1000
MAX_FILES_PER_UPLOAD = 5
MAX_FILE_SIZE_MB = 10
MAX_REQUESTS_PER_MINUTE = 10

MAX_FILE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _normalize(text: Optional[str]) -> str:
    """Unicode-normalize, drop control characters, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _CONTROL_CHARS.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


# Phrases that try to take over the model's instructions.
_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bignore\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|above|prior|earlier)\s+(?:instructions?|prompts?|rules?)",
        r"\bdisregard\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|above|prior|earlier)\s+(?:instructions?|prompts?|rules?)",
        r"\b(?:reveal|show|print|repeat|tell\s+me)\s+(?:me\s+)?(?:your|the|its|all)\s+(?:system\s+prompt|hidden\s+prompt|instructions|api[\s_\-]?keys?)",
        r"\bact\s+as\s+(?:if\s+)?you\s+(?:have|had)\s+no\s+(?:rules|restrictions|limits)",
        r"\bdeveloper\s+mode\b",
        r"\bjailbreak\b",
    )
]


def looks_like_injection(text: str) -> bool:
    t = _normalize(text)
    return any(p.search(t) for p in _INJECTION_PATTERNS)


# --------------------------------------------------------------------------
# Checkpoint 3: user question
# --------------------------------------------------------------------------
def check_user_input(text: Optional[str]) -> Tuple[bool, str]:
    """Return (ok, message). If ok is False, show `message` to the user."""
    t = _normalize(text)
    if not t:
        return False, "Please type a question."
    if len(t) > MAX_QUESTION_CHARS:
        return False, f"Please keep your question under {MAX_QUESTION_CHARS} characters."
    if looks_like_injection(t):
        return False, "I can't help with that request."
    return True, ""


class RateLimiter:
    """Sliding-window limiter, one window per key (for example a session id).

    In-memory only: it resets when the app restarts and is not shared between
    several worker processes. Use Redis or similar if you run many workers.
    """

    def __init__(self, max_calls: int = MAX_REQUESTS_PER_MINUTE, window_seconds: int = 60):
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> Tuple[bool, int]:
        """Return (allowed, seconds_to_wait)."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.max_calls:
                wait = int(self.window - (now - hits[0])) + 1
                return False, max(wait, 1)
            hits.append(now)
            return True, 0


rate_limiter = RateLimiter()


# --------------------------------------------------------------------------
# Checkpoint 1: uploads
# --------------------------------------------------------------------------
def limit_upload_count(files: Iterable) -> Tuple[list, int]:
    """Keep at most MAX_FILES_PER_UPLOAD files. Return (kept_files, skipped_count)."""
    files = list(files)
    return files[:MAX_FILES_PER_UPLOAD], max(0, len(files) - MAX_FILES_PER_UPLOAD)


_ZIP_MAGIC = [b"PK\x03\x04"]
_SIGNATURES = {
    ".docx": _ZIP_MAGIC,
    ".pptx": _ZIP_MAGIC,
    ".xlsx": _ZIP_MAGIC,
    ".xls": [b"\xd0\xcf\x11\xe0"],
    ".db": [b"SQLite format 3"],
    ".sqlite": [b"SQLite format 3"],
    ".sqlite3": [b"SQLite format 3"],
}
_TEXT_EXTS = {".txt", ".md", ".csv"}


def check_saved_file(path: Path) -> Tuple[bool, str]:
    """Check a file AFTER it was written to disk: not empty, not too big,
    and the content matches its extension (a renamed file is rejected)."""
    path = Path(path)
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            head = f.read(2048)
    except OSError:
        return False, "File could not be read"

    if size == 0:
        return False, "File is empty"
    if size > MAX_FILE_BYTES:
        return False, f"File is larger than {MAX_FILE_SIZE_MB} MB"

    ext = path.suffix.lower()
    if ext == ".pdf":
        if b"%PDF" not in head[:1024]:
            return False, "Content does not look like a real PDF"
    elif ext in _SIGNATURES:
        if not any(head.startswith(sig) for sig in _SIGNATURES[ext]):
            return False, f"Content does not look like a real {ext} file"
    elif ext in _TEXT_EXTS:
        if b"\x00" in head:
            return False, "Text file contains binary data"
    return True, ""


# --------------------------------------------------------------------------
# Checkpoint 2: document chunks (before they go into the index)
# --------------------------------------------------------------------------
def filter_chunks(chunks: Iterable) -> Tuple[List, int]:
    """Drop chunks that contain instruction-hijacking phrases.
    Returns (kept_chunks, dropped_count). Works with LangChain Document objects.

    Trade-off: a legitimate document ABOUT prompt injection may lose a few
    chunks. The caller should log the dropped count.
    """
    kept: List = []
    dropped = 0
    for c in chunks:
        text = getattr(c, "page_content", None) or str(c)
        if looks_like_injection(text):
            dropped += 1
        else:
            kept.append(c)
    return kept, dropped


# --------------------------------------------------------------------------
# Checkpoint 6: output
# --------------------------------------------------------------------------
# Common API-key shapes (Groq, Google, OpenAI-style, HuggingFace, AWS).
_SECRET_PATTERNS = [
    re.compile(p)
    for p in (
        r"\bgsk_[A-Za-z0-9]{20,}",
        r"\bAIza[0-9A-Za-z_\-]{30,}",
        r"\bAQ\.[A-Za-z0-9_\-]{20,}",
        r"\bsk-[A-Za-z0-9_\-]{20,}",
        r"\bhf_[A-Za-z0-9]{20,}",
        r"\bAKIA[0-9A-Z]{16}\b",
    )
]
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Indian mobile numbers (10 digits starting 6-9, optional +91) and +country-code numbers.
# A plain 10-digit number starting with 6-9 will also match; that is the trade-off.
_PHONE_IN = re.compile(r"(?<![\w.])(?:\+91[\s\-]?)?[6-9]\d{9}(?![\w.])")
_PHONE_INTL = re.compile(r"(?<![\w.])\+\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,4}(?![\w.])")


def sanitize_output(text: str, mask_emails: bool = True, mask_phones: bool = True) -> str:
    """Mask API keys always; mask emails and phone numbers by default.
    Set mask_emails=False if answers may legitimately contain emails
    (for example the author emails printed on a paper)."""
    if not text:
        return text
    for p in _SECRET_PATTERNS:
        text = p.sub("[REDACTED_KEY]", text)
    if mask_emails:
        text = _EMAIL.sub("[EMAIL]", text)
    if mask_phones:
        text = _PHONE_INTL.sub("[PHONE]", text)
        text = _PHONE_IN.sub("[PHONE]", text)
    return text


# --------------------------------------------------------------------------
# One call that applies the checks around rag.invoke()
# --------------------------------------------------------------------------
def guarded_invoke(rag, question: str, chat_history=None, session_id: Optional[str] = None) -> str:
    """Input check -> rate limit -> rag.invoke -> output masking.

    Blocked questions return a polite message and never reach the LLM,
    so they use no API quota.
    """
    ok, msg = check_user_input(question)
    if not ok:
        return msg

    key = session_id or getattr(rag, "session_id", None) or "anonymous"
    allowed, wait = rate_limiter.allow(str(key))
    if not allowed:
        return f"You are sending questions too quickly. Please wait {wait} seconds and try again."

    answer = rag.invoke(question, chat_history=chat_history)
    return sanitize_output(answer)