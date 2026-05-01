from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PageRef:
    page_number: int
    label: str  # ex: "Planilha1", "Seção 2", "Página 3"


@dataclass
class ParsedDocument:
    text: str
    page_map: list[PageRef] = field(default_factory=list)
    doc_type: str = ""
    original_filename: str = ""


class BaseParser(ABC):
    @abstractmethod
    def parse(self, file_path: Path) -> ParsedDocument:
        ...

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.extensions

    @property
    @abstractmethod
    def extensions(self) -> list[str]:
        ...
