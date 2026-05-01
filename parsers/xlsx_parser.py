from pathlib import Path

import pandas as pd

from parsers.base import BaseParser, PageRef, ParsedDocument
from parsers.csv_parser import CSVParser

MAX_ROWS_PREVIEW = 500


class XLSXParser(BaseParser):
    @property
    def extensions(self) -> list[str]:
        return [".xlsx", ".xls"]

    def parse(self, file_path: Path) -> ParsedDocument:
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
