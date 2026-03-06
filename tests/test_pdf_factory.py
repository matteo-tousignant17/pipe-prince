import os
import pytest
from services.pdf_factory import (
    PDFGenerationError,
    _build_pdf_filename,
    generate_rate_confirmation,
    generate_release_document,
)


def test_build_pdf_filename_rate_con():
    path = _build_pdf_filename("LS-2026-001", "rate_con")
    assert path.endswith("LS-2026-001_rate_con.pdf")


def test_build_pdf_filename_release_doc():
    path = _build_pdf_filename("LS-2026-001", "release_doc")
    assert path.endswith("LS-2026-001_release_doc.pdf")


def test_generate_rate_confirmation_creates_file(tmp_path, make_test_load, monkeypatch):
    monkeypatch.setattr("services.pdf_factory.PDF_OUTPUT_DIR", str(tmp_path))
    load = make_test_load()
    path = generate_rate_confirmation(load)
    assert os.path.isfile(path)
    assert os.path.getsize(path) > 1000


def test_generate_release_document_creates_file(tmp_path, make_test_load, monkeypatch):
    monkeypatch.setattr("services.pdf_factory.PDF_OUTPUT_DIR", str(tmp_path))
    load = make_test_load()
    path = generate_release_document(load)
    assert os.path.isfile(path)
    assert os.path.getsize(path) > 1000


def test_rate_confirmation_is_pdf(tmp_path, make_test_load, monkeypatch):
    monkeypatch.setattr("services.pdf_factory.PDF_OUTPUT_DIR", str(tmp_path))
    load = make_test_load()
    path = generate_rate_confirmation(load)
    with open(path, "rb") as f:
        header = f.read(4)
    assert header == b"%PDF"


def test_release_document_is_pdf(tmp_path, make_test_load, monkeypatch):
    monkeypatch.setattr("services.pdf_factory.PDF_OUTPUT_DIR", str(tmp_path))
    load = make_test_load()
    path = generate_release_document(load)
    with open(path, "rb") as f:
        header = f.read(4)
    assert header == b"%PDF"


def test_rate_confirmation_with_anomaly_flag(tmp_path, make_test_load, monkeypatch):
    """Anomaly-flagged loads should still generate a valid PDF."""
    monkeypatch.setattr("services.pdf_factory.PDF_OUTPUT_DIR", str(tmp_path))
    load = make_test_load(rate_anomaly=True, rate_anomaly_pct=0.15)
    path = generate_rate_confirmation(load)
    assert os.path.isfile(path)


def test_rate_confirmation_no_miles(tmp_path, make_test_load, monkeypatch):
    """Load with zero miles (unknown) should generate a valid PDF with N/A cost."""
    from models.load import RouteInfo
    monkeypatch.setattr("services.pdf_factory.PDF_OUTPUT_DIR", str(tmp_path))
    load = make_test_load(
        route=RouteInfo(origin="Oklahoma City, OK", destination="Dallas, TX", total_miles=0),
        cost_per_mile=None,
    )
    path = generate_rate_confirmation(load)
    assert os.path.isfile(path)
