from pathlib import Path

import pymupdf

from parsers.base import BaseParser, PageRef, ParsedDocument


class PDFParser(BaseParser):
    @property
    def extensions(self) -> list[str]:
        return [".pdf"]

    def parse(self, file_path: Path) -> ParsedDocument:
        doc = pymupdf.open(str(file_path))
        pages_text = []
        page_map = []

        for i, page in enumerate(doc):
            text = page.get_text().strip()
            if text:
                page_number = i + 1
                pages_text.append(f"## Página {page_number}\n{text}")
                page_map.append(PageRef(page_number=page_number, label=f"Página {page_number}"))

        doc.close()

        return ParsedDocument(
            text="\n\n".join(pages_text),
            page_map=page_map,
            doc_type="pdf",
            original_filename=file_path.name,
        )
