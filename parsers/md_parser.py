import re
from pathlib import Path

from parsers.base import BaseParser, PageRef, ParsedDocument

# Each heading level becomes its own page/node in the index.
# H1-H4 gives fine-grained nodes so that entries like individual crops or
# cultivars don't get merged into one giant block that causes LLM cross-contamination.
_HEADING_RE = re.compile(r"^#{1,4} .+", re.MULTILINE)


class MDParser(BaseParser):
    @property
    def extensions(self) -> list[str]:
        return [".md"]

    def parse(self, file_path: Path) -> ParsedDocument:
        text = file_path.read_text(encoding="utf-8")

        matches = list(_HEADING_RE.finditer(text))

        if not matches:
            return ParsedDocument(
                text=text,
                page_map=[PageRef(page_number=1, label="Documento")],
                doc_type="md",
                original_filename=file_path.name,
            )

        page_map: list[PageRef] = []
        sections: list[str] = []

        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            section_text = text[start:end].strip()
            label = match.group(0).lstrip("#").strip()
            sections.append(section_text)
            page_map.append(PageRef(page_number=i + 1, label=label))

        return ParsedDocument(
            text="\n\n".join(sections),
            page_map=page_map,
            doc_type="md",
            original_filename=file_path.name,
        )
