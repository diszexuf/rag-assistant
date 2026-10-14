from app.nc.dialect import load_dialect
from app.nc.types import ConversionResult, Unconverted


def convert(text: str, src_dialect: str, dst_dialect: str) -> ConversionResult:
    """Заглушка этапа 1: ничего не переводит и честно сообщает об этом."""
    load_dialect(src_dialect)
    load_dialect(dst_dialect)
    if src_dialect == dst_dialect:
        return ConversionResult(text=text)
    unconverted = [
        Unconverted(line, "конвертер ещё не реализован")
        for line, raw in enumerate(text.splitlines(), start=1)
        if raw.strip()
    ]
    return ConversionResult(text=text, unconverted=unconverted)
