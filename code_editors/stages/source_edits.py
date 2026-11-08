"""Indexed AST byte positions and non-overlapping text replacements."""
from __future__ import annotations
import re


class SourceIndex:
    """Translate AST UTF-8 columns without rescanning source for every edit."""

    def __init__(self, source: str):
        # AST line numbers count CR/LF, not form feeds or Unicode separators
        # that may legally occur inside literals or indentation.
        chunks = re.split(r"(\r\n|\n|\r)", source)
        self.lines = [chunks[i] + (chunks[i + 1] if i + 1 < len(chunks) else "")
                      for i in range(0, len(chunks), 2)]
        self.encoded_lines = [line.encode("utf-8") for line in self.lines]
        self.starts = []
        self.columns = []
        offset = 0
        for line in self.lines:
            self.starts.append(offset)
            offset += len(line)
            if line.isascii():
                self.columns.append(None)
            else:
                columns = {0: 0}
                byte = 0
                for column, character in enumerate(line, 1):
                    byte += len(character.encode("utf-8"))
                    columns[byte] = column
                self.columns.append(columns)

    def offset(self, line: int, byte: int) -> int:
        columns = self.columns[line - 1]
        return self.starts[line - 1] + (byte if columns is None else columns[byte])

    def segment(self, node) -> str:
        if node.lineno == node.end_lineno:
            return self.encoded_lines[node.lineno - 1][node.col_offset:node.end_col_offset].decode("utf-8")
        raise ValueError("Expected a single-line token")


def apply_edits(source: str, edits: list[tuple[int, int, str]]) -> str:
    pieces = []
    cursor = 0
    for start, end, replacement in sorted(edits):
        if start < cursor or end < start:
            raise ValueError("Source edits overlap")
        pieces.extend((source[cursor:start], replacement))
        cursor = end
    pieces.append(source[cursor:])
    return "".join(pieces)
