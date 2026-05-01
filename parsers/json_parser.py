import json
from pathlib import Path

from parsers.base import BaseParser, PageRef, ParsedDocument


def _json_to_text(data, indent: int = 0) -> str:
    prefix = "  " * indent
    lines = []

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}**{key}:**")
                lines.append(_json_to_text(value, indent + 1))
            else:
                lines.append(f"{prefix}- **{key}:** {value}")
    elif isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}Item {i + 1}:")
                lines.append(_json_to_text(item, indent + 1))
            else:
                lines.append(f"{prefix}- {item}")
    else:
        lines.append(f"{prefix}{data}")

    return "\n".join(lines)


class JSONParser(BaseParser):
    @property
    def extensions(self) -> list[str]:
        return [".json"]

    def parse(self, file_path: Path) -> ParsedDocument:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            sections = []
            for i, item in enumerate(data, start=1):
                sections.append(f"## Item {i}\n{_json_to_text(item)}")
            text = f"# {file_path.name}\n\n" + "\n\n".join(sections)
            page_map = [PageRef(page_number=i, label=f"Item {i}") for i in range(1, len(data) + 1)]
        else:
            text = f"# {file_path.name}\n\n{_json_to_text(data)}"
            page_map = [PageRef(page_number=1, label="Documento")]

        return ParsedDocument(
            text=text,
            page_map=page_map,
            doc_type="json",
            original_filename=file_path.name,
        )
