from pathlib import Path

from pdf_ocr_router.backends import DoclingBackend, docling_environment, project_root


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


def test_docling_environment_keeps_cache_inside_skill(monkeypatch):
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    environment = docling_environment()

    assert environment["HF_HOME"] == str(project_root() / ".cache" / "huggingface")
    assert environment["XDG_CACHE_HOME"] == str(project_root() / ".cache" / "docling")
