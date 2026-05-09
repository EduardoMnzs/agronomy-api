from pathlib import Path

# Side-effect: protege o parser de openpyxl contra billion-laughs/XXE.
import defusedxml.ElementTree  # noqa: F401

import pandas as pd

from parsers.base import BaseParser, PageRef, ParsedDocument
from parsers.csv_parser import CSVParser
from parsers.safety import assert_zip_safe

MAX_ROWS_PREVIEW = 500


class XLSXParser(BaseParser):
    @property
    def extensions(self) -> list[str]:
        return [".xlsx", ".xls"]

    def parse(self, file_path: Path) -> ParsedDocument:
        # .xls é binário OLE (não-ZIP) — só checa bomb em .xlsx.
        if file_path.suffix.lower() == ".xlsx":
            assert_zip_safe(file_path)
        xl = pd.ExcelFile(str(file_path))
        sections: list[str] = []
        page_map: list[PageRef] = []

        for page_number, sheet_name in enumerate(xl.sheet_names, start=1):
            df = xl.parse(sheet_name)
            parsed = CSVParser._df_to_parsed(df, sheet_name, "xlsx")

            sections.append(f"# Planilha: {sheet_name}\n\n{parsed.text}")
            page_map.append(PageRef(page_number=page_number, label=f"Planilha: {sheet_name}"))

        return ParsedDocument(
            text="\n\n---\n\n".join(sections),
            page_map=page_map,
            doc_type="xlsx",
            original_filename=file_path.name,
        )
