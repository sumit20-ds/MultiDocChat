import os
import io
import types
import json
import shutil
import pathlib
import sys
import pytest


os.environ.setdefault("PYTHONPATH", str(pathlib.Path(__file__).resolve().parents[1] / "multi_doc_chat"))
os.environ.setdefault("GROQ_API_KEY", "dummy")
os.environ.setdefault("GOOGLE_API_KEY", "dummy")
os.environ.setdefault("LLM_PROVIDER", "google")

from fastapi.testclient import TestClient

# Ensure repository root is importable for `import main`
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main

"""
The os.environ.setdefault(...) lines put fake API keys ("dummy") in place, so the app doesn't complain about missing keys. The sys.path.insert line makes sure Python can find your main.py."""


@pytest.fixture
def client():
    return TestClient(main.app)

"""
TestClient(main.app) is a pretend user who can call /upload, /chat and so on without starting a real server. It's like a test driver who sits in the car without needing a real road."""


@pytest.fixture
def clear_sessions():
    main.SESSIONS.clear()
    yield
    main.SESSIONS.clear()

"""
clear_sessions (wiping the whiteboard)

Your app remembers chats in the SESSIONS dictionary. This fixture empties it before and after each test, like erasing the whiteboard so the next student doesn't see the last student's notes."""


@pytest.fixture
def tmp_dirs(tmp_path: pathlib.Path):
    data_dir = tmp_path / "data"
    faiss_dir = tmp_path / "faiss_index"
    data_dir.mkdir(parents=True, exist_ok=True)
    faiss_dir.mkdir(parents=True, exist_ok=True)
    cwd = pathlib.Path.cwd()
    try:
        # Point working directories used by app code to tmp ones by chdir
        os.chdir(tmp_path)
        yield {"data": data_dir, "faiss": faiss_dir}
    finally:
        os.chdir(cwd)

"""
tmp_dirs (a throwaway workbench)

It creates temporary data and faiss_index folders and moves into them. Anything the app saves lands there and is deleted afterward, so your real project folders stay clean."""


#the dummy parts of embeddings and LLM models
class _StubEmbeddings:
    def embed_query(self, text: str):
        return [0.0, 0.1, 0.2]

    def embed_documents(self, texts):
        return [[0.0, 0.1, 0.2] for _ in texts]

    def __call__(self, text: str):
        return [0.0, 0.1, 0.2]


class _StubLLM:
    def invoke(self, input):
        return "stubbed answer"


@pytest.fixture
def stub_model_loader(monkeypatch):
    # Patch both module paths to cover imports via `utils.model_loader` and `multi_doc_chat.utils.model_loader`
    import multi_doc_chat.utils.model_loader as ml_mod
    from multi_doc_chat.utils import model_loader as ml_mod2

    class FakeApiKeyMgr:
        def __init__(self):
            self.api_keys = {"GROQ_API_KEY": "x", "GOOGLE_API_KEY": "y"}

        def get(self, key: str) -> str:
            return self.api_keys[key]

    class FakeModelLoader:
        def __init__(self):
            self.api_key_mgr = FakeApiKeyMgr()
            self.config = {
                "embedding_model": {"model_name": "fake-embed"},
                "llm": {
                    "google": {
                        "provider": "google",
                        "model_name": "fake-llm",
                        "temperature": 0.0,
                        "max_output_tokens": 128,
                    }
                },
            }

        def load_embeddings(self):
            return _StubEmbeddings()

        def load_llm(self):
            return _StubLLM()

    monkeypatch.setattr(ml_mod, "ApiKeyManager", FakeApiKeyMgr)
    monkeypatch.setattr(ml_mod, "ModelLoader", FakeModelLoader)
    monkeypatch.setattr(ml_mod2, "ApiKeyManager", FakeApiKeyMgr)
    monkeypatch.setattr(ml_mod2, "ModelLoader", FakeModelLoader)

    # Also patch the already-imported symbols used in modules under test
    import multi_doc_chat.src.document_ingestion.data_ingestion as di
    import multi_doc_chat.src.document_chat.retrieval as r
    monkeypatch.setattr(di, "ModelLoader", FakeModelLoader)
    monkeypatch.setattr(r, "ModelLoader", FakeModelLoader)
    yield FakeModelLoader


"""
stub_model_loader (swapping the engine)

This is the trickiest one. monkeypatch temporarily replaces the real ModelLoader and ApiKeyManager with fakes everywhere they're imported. It patches both import paths (utils.model_loader and multi_doc_chat.utils.model_loader), because Python can hold the same class under two names. It's like replacing the engine in every place the car's manual mentions one."""


@pytest.fixture
def stub_ingestor(monkeypatch):
    import multi_doc_chat.src.document_ingestion.data_ingestion as di

    class FakeIngestor:
        def __init__(self, use_session_dirs=True, **kwargs):
            self.use_session = use_session_dirs
            self.session_id = "sess_test"

        def built_retriver(self, uploaded_files, **kwargs):
            return None

    monkeypatch.setattr(di, "ChatIngestor", FakeIngestor)
    monkeypatch.setattr(main, "ChatIngestor", FakeIngestor)
    yield FakeIngestor


"""
stub_ingestor (a fake librarian)

The real ChatIngestor reads files, splits them, embeds them and builds a FAISS index. FakeIngestor skips all of that: it does nothing and always hands back the session ID "sess_test". Now you can test whether /upload works without any real indexing."""


@pytest.fixture
def stub_rag(monkeypatch):
    import multi_doc_chat.src.document_chat.retrieval as r

    class FakeRAG:
        def __init__(self, session_id=None, retriever=None):
            self.session_id = session_id
            self.retriever = retriever

        def load_retriever_from_faiss(self, index_path, **kwargs):
            return None

        def invoke(self, user_input, chat_history=None):
            return "stubbed answer"


    monkeypatch.setattr(r, "ConversationalRAG", FakeRAG)
    monkeypatch.setattr(main, "ConversationalRAG", FakeRAG)
    yield FakeRAG


"""
stub_rag (a fake answering machine)

The real ConversationalRAG searches your documents and asks the LLM. FakeRAG skips the search and always replies "stubbed answer". That lets you test whether /chat handles sessions, history and errors correctly."""