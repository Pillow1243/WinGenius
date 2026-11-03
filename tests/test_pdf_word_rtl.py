"""Regression tests for editable PDF text and encrypted/scanned documents."""
from pathlib import Path

import pytest
from docx import Document
from pypdf import PdfWriter

from app.core.spec import RunContext, ToolError
from app.tools import pdf_tools


def test_word_rtl_and_partial_scan(tmp_path, monkeypatch):
    class Page:
        def __init__(self, text):
            self.text = text
        def extract_text(self):
            return self.text
    class Reader:
        pages = [Page('سلام دنیا 123\nEnglish text'), Page('')]
    src = tmp_path / 'source.pdf'
    src.touch()
    monkeypatch.setattr(pdf_tools, '_open_reader', lambda *_: Reader())
    result = pdf_tools.pdf_to_docx(RunContext(lang='en', output_dir=str(tmp_path)), {'src': str(src)})
    paragraphs = Document(result.outputs[0]).paragraphs
    assert paragraphs[0].text == 'سلام دنیا 123'
    assert paragraphs[0]._p.pPr.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}bidi') is not None
    assert paragraphs[1].text == 'English text'
    assert 'Pages without extractable text: 2' in result.message


def test_scan_does_not_create_misleading_word_file(tmp_path, monkeypatch):
    class Page:
        def extract_text(self):
            return ''
    class Reader:
        pages = [Page()]
    src = tmp_path / 'scan.pdf'
    src.touch()
    monkeypatch.setattr(pdf_tools, '_open_reader', lambda *_: Reader())
    with pytest.raises(ToolError, match='OCR'):
        pdf_tools.pdf_to_docx(RunContext(lang='en', output_dir=str(tmp_path)), {'src': str(src)})
    assert not list(tmp_path.glob('*.docx'))


def test_password_protected_pdf_reports_password(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt('secret')
    src = tmp_path / 'protected.pdf'
    with src.open('wb') as f:
        writer.write(f)
    with pytest.raises(ToolError, match='password protected'):
        pdf_tools._open_reader(RunContext(lang='en', output_dir=str(tmp_path)), src)
