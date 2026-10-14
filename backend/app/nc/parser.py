import re

from app.nc.dialect import Dialect, load_dialect
from app.nc.types import Block, ParsedProgram, Word

_NUMBER = r"[+-]?(?:\d+\.?\d*|\.\d+)"
_WORD = re.compile(rf"([A-Z])\s*({_NUMBER})")
_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
_CALL_ARGS = re.compile(r"\s*\(([^)]*)\)")
_ASSIGN_VALUE = re.compile(rf"\s*=\s*(\"[^\"]*\"|{_NUMBER}|[A-Z_][A-Z0-9_]*)")
_LABEL = re.compile(r"\s*[A-Z_][A-Z0-9_]*:")
_PAREN_COMMENT = re.compile(r"\(([^)]*)\)")


def normalize_code(letter: str, value: str) -> str:
    """G01 -> G1, M03 -> M3, G07.1 -> G7.1."""
    sign = "-" if value.startswith("-") else ""
    digits = value.lstrip("+-")
    integer, dot, fraction = digits.partition(".")
    integer = integer.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    return f"{letter}{sign}{integer}" + (f".{fraction}" if dot and fraction else "")


def parse(text: str, dialect: str) -> ParsedProgram:
    spec = load_dialect(dialect)
    parse_line = _parse_fanuc_line if spec.syntax == "fanuc" else _parse_sinumerik_line
    blocks: list[Block] = []
    for index, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.strip() == "%":
            continue
        block = Block(line=index, raw=raw)
        parse_line(raw.upper(), block, spec)
        if block.words or block.commands or block.unknown_names or block.error:
            blocks.append(block)
    return ParsedProgram(dialect=dialect, blocks=blocks)


def _add_word(block: Block, letter: str, value: str) -> None:
    if letter == "N" and block.number is None and not block.words and not block.commands:
        block.number = int(float(value))
    elif letter in "GM":
        block.commands.append(normalize_code(letter, value))
    else:
        block.words.append(Word(letter, value))


def _parse_fanuc_line(line: str, block: Block, spec: Dialect) -> None:
    comments = _PAREN_COMMENT.findall(line)
    block.comment = " ".join(c.strip() for c in comments)
    code = _PAREN_COMMENT.sub(" ", line).strip()
    if "(" in code or ")" in code:
        block.error = "непарная скобка комментария"
        return
    pos = 1 if code.startswith("/") else 0
    while pos < len(code):
        if code[pos].isspace():
            pos += 1
            continue
        match = _WORD.match(code, pos)
        if not match:
            block.error = f"не удалось разобрать: «{code[pos:].strip()}»"
            return
        _add_word(block, match.group(1), match.group(2))
        pos = match.end()


def _parse_sinumerik_line(line: str, block: Block, spec: Dialect) -> None:
    code, _, comment = _split_semicolon(line)
    block.comment = comment.strip()
    label = _LABEL.match(code)
    pos = label.end() if label else 0
    while pos < len(code):
        if code[pos].isspace():
            pos += 1
            continue
        # Однобуквенный адрес с числом: G1, X10.5, S 150
        if not (pos + 1 < len(code) and (code[pos + 1].isalpha() or code[pos + 1] == "_")):
            match = _WORD.match(code, pos)
            if match:
                _add_word(block, match.group(1), match.group(2))
                pos = match.end()
                continue
        name_match = _NAME.match(code, pos)
        if not name_match:
            block.error = f"не удалось разобрать: «{code[pos:].strip()}»"
            return
        name = name_match.group(0)
        pos = name_match.end()
        call = _CALL_ARGS.match(code, pos)
        assign = _ASSIGN_VALUE.match(code, pos)
        if call:
            block.words.append(Word(f"{name}()", call.group(1)))
            pos = call.end()
            _add_name(block, name, spec)
        elif assign:
            value = assign.group(1)
            pos = assign.end()
            if name in ("G", "M"):
                block.commands.append(normalize_code(name, value))
            else:
                block.words.append(Word(name, value))
        else:
            _add_name(block, name, spec)


def _add_name(block: Block, name: str, spec: Dialect) -> None:
    if name in spec.commands:
        block.commands.append(name)
    else:
        block.unknown_names.append(name)


def _split_semicolon(line: str) -> tuple[str, str, str]:
    in_quotes = False
    for index, char in enumerate(line):
        if char == '"':
            in_quotes = not in_quotes
        elif char == ";" and not in_quotes:
            return line[:index], ";", line[index + 1 :]
    return line, "", ""
