from pathlib import Path

from parsers.base import BaseParser
from parsers.csv_parser import CSVParser
from parsers.docx_parser import DOCXParser
from parsers.json_parser import JSONParser
from parsers.pdf_parser import PDFParser
from parsers.xlsx_parser import XLSXParser

_PARSERS: list[BaseParser] = [
    PDFParser(),
    DOCXParser(),
    CSVParser(),
    XLSXParser(),
    JSONParser(),
]

SUPPORTED_EXTENSIONS = {ext for p in _PARSERS for ext in p.extensions}


def get_parser(file_path: Path) -> BaseParser:
    for parser in _PARSERS:
        if parser.supports(file_path):
            return parser
    raise ValueError(f"Formato não suportado: {file_path.suffix}. Formatos aceitos: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
