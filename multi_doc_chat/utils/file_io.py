
from __future__ import annotations
import re
import uuid #helps to make a random id (128 bit)
from pathlib import Path
from typing import Iterable, List
from multi_doc_chat.logger.custom_logger import CustomLogger
from multi_doc_chat.exception.custom_exception import DocumentPortalException
from multi_doc_chat.utils.guardrails import limit_upload_count, check_saved_file

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".pptx", ".md", ".csv", ".xlsx", ".xls", ".db", ".sqlite", ".sqlite3"}

# Local logger instance
log = CustomLogger().get_logger(__name__)


def save_uploaded_files(uploaded_files: Iterable, target_dir: Path) -> List[Path]:
    """Save uploaded files (Streamlit-like) and return local paths."""
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        saved: List[Path] = []
        uploaded_files, skipped = limit_upload_count(uploaded_files)
        if skipped:
            log.warning("Too many files; extra files skipped", skipped=skipped)

        for uf in uploaded_files:  #uf - uploaded file
            # Handle Starlette UploadFile (has .filename and .file) and generic objects (have .name)
            name = getattr(uf, "filename", getattr(uf, "name", "file"))     #getattr - give me it or backup
            ext = Path(name).suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                log.warning("Unsupported file skipped", filename=name)
                continue
            # Clean file name (only alphanum, dash, underscore)
            safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', Path(name).stem).lower()
            fname = f"{safe_name}_{uuid.uuid4().hex[:6]}{ext}"
            fname = f"{uuid.uuid4().hex[:8]}{ext}"
            out = target_dir / fname
            with open(out, "wb") as f:
                # Prefer underlying file buffer when available (e.g., Starlette UploadFile.file)
                if hasattr(uf, "file") and hasattr(uf.file, "read"):      #hasattr - does it have this
                    f.write(uf.file.read())
                elif hasattr(uf, "read"):
                    data = uf.read()
                    # If a memoryview is returned, convert to bytes; otherwise assume bytes
                    if isinstance(data, memoryview):
                        data = data.tobytes()
                    f.write(data)
                else:
                    # Fallback for objects exposing a getbuffer()
                    buf = getattr(uf, "getbuffer", None)
                    if callable(buf):     #callable - can i run it like a fuction?
                        data = buf()
                        if isinstance(data, memoryview):
                            data = data.tobytes()
                        f.write(data)
                    else:
                        raise ValueError("Unsupported uploaded file object; no readable interface")
                    ok, reason = check_saved_file(out)

                    if not ok:
                       out.unlink(missing_ok=True)
                       log.warning("Upload rejected", filename=name, reason=reason)
                       continue

            saved.append(out)
            log.info("File saved for ingestion", uploaded=name, saved_as=str(out))
        return saved
    except Exception as e:
        log.error("Failed to save uploaded files", error=str(e), dir=str(target_dir))
        raise DocumentPortalException("Failed to save uploaded files", e) from e





"""
4. Reading the bytes (three styles)

Upload type	How it reads
Has .file.read()    -- 	FastAPI's UploadFile
Has .read()	Generic --  file-like objects
Has .getbuffer()	--  Streamlit's uploaded file

"""