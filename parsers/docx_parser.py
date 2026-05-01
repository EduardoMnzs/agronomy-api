from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

from parsers.base import BaseParser, PageRef, ParsedDocument


def _table_to_markdown(table) -> str:
    rows = []
    for i, row in enumerate(table.rows):
        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
        rows.append("| " + " | ".join(cells) + " |")
        if i == 0:
            rows.append("|" + "|".join(["---"] * len(cells)) + "|")
    return "\n".join(rows)


class DOCXParser(BaseParser):
    @property
    def extensions(self) -> list[str]:
        return [".docx"]

    def parse(self, file_path: Path) -> ParsedDocument:
        doc = Document(str(file_path))
        sections: list[str] = []
        page_map: list[PageRef] = []
        current_section: list[str] = []
        section_number = 1

        for block in doc.element.body:
            tag = block.tag.split("}")[-1] if "}" in block.tag else block.tag

            if tag == "p":
                para_text = "".join(node.text or "" for node in block.iter() if node.tag.endswith("}t"))
                style = block.get(qn("w:styleId"), "")

                if style and "Heading" in style and para_text.strip():
                    if current_section:
                        sections.append("\n".join(current_section))
                        page_map.append(PageRef(page_number=section_number, label=f"Seção {section_number}"))
                        section_number += 1
                        current_section = []
                    level = "".join(filter(str.isdigit, style)) or "1"
                    current_section.append("#" * int(level) + " " + para_text.strip())
                elif para_text.strip():
                    current_section.append(para_text.strip())

            elif tag == "tbl":
                for tbl in doc.tables:
                    current_section.append(_table_to_markdown(tbl))
                    break

        if current_section:
            sections.append("\n".join(current_section))
            page_map.append(PageRef(page_number=section_number, label=f"Seção {section_number}"))

        return ParsedDocument(
            text="\n\n".join(sections),
            page_map=page_map,
            doc_type="docx",
            original_filename=file_path.name,
        )
