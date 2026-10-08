from pathlib import Path

from pdf_ocr_router.backends import DoclingBackend, docling_environment


def test_docling_command_uses_local_structural_pipeline(monkeypatch, tmp_path):
    backend = DoclingBackend()
    monkeypatch.setattr(backend, "_docling", lambda: "/opt/docling/bin/docling")
    source = tmp_path / "input.pdf"
    command = backend._command(source, tmp_path / "output")

    assert command[:3] == ["/opt/docling/bin/docling", "convert", str(source)]
    assert command[command.index("--pipeline") + 1] == "standard"
    assert command[command.index("--ocr-mode") + 1] == "full_page"
    assert command[command.index("--ocr-engine") + 1] == "rapidocr"
    assert command[command.index("--ocr-lang") + 1] == "ch"
    assert "--tables" in command
    assert command[command.index("--device") + 1] == "cpu"


def test_docling_environment_uses_working_directory_cache(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("PDF2MD_CACHE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    environment = docling_environment()

    cache_root = tmp_path / ".cache" / "pdf2md"
    assert environment["HF_HOME"] == str(cache_root / "huggingface")
    assert environment["XDG_CACHE_HOME"] == str(cache_root / "docling")


def test_docling_environment_accepts_explicit_cache(monkeypatch, tmp_path):
    cache_root = tmp_path / "shared-cache"
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("PDF2MD_CACHE_DIR", str(cache_root))
    environment = docling_environment()

    assert environment["HF_HOME"] == str(cache_root / "huggingface")
    assert environment["XDG_CACHE_HOME"] == str(cache_root / "docling")
