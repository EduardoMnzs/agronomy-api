from pathlib import Path

import pandas as pd

from parsers.base import BaseParser, PageRef, ParsedDocument

MAX_ROWS_PREVIEW = 500


class CSVParser(BaseParser):
    @property
    def extensions(self) -> list[str]:
        return [".csv"]

    def parse(self, file_path: Path) -> ParsedDocument:
        df = pd.read_csv(file_path, encoding="utf-8", on_bad_lines="skip")
        return self._df_to_parsed(df, file_path.name, "csv")

    @staticmethod
    def _df_to_parsed(df: pd.DataFrame, filename: str, doc_type: str) -> ParsedDocument:
        df = df.fillna("").astype(str)

        if len(df) > MAX_ROWS_PREVIEW:
            note = f"\n\n> ⚠️ Tabela truncada: exibindo {MAX_ROWS_PREVIEW} de {len(df)} linhas."
            df = df.head(MAX_ROWS_PREVIEW)
        else:
            note = ""

        header = "| " + " | ".join(df.columns) + " |"
        separator = "|" + "|".join(["---"] * len(df.columns)) + "|"
        rows = ["| " + " | ".join(row) + " |" for row in df.itertuples(index=False, name=None)]

        table_md = "\n".join([header, separator] + rows) + note

        text = f"# {filename}\n\n{table_md}"
        page_map = [PageRef(page_number=1, label="Tabela principal")]

        return ParsedDocument(
            text=text,
            page_map=page_map,
            doc_type=doc_type,
            original_filename=filename,
        )
