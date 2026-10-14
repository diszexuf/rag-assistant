from pathlib import Path

import pytest
import yaml

from app.kb.split import split_program, template_description
from seed.seed import assemble

SEED_DIR = Path(__file__).parent.parent / "seed" / "programs"
MACHINE_DIALECTS = {
    "Токарный станок с ЧПУ Fanuc 0i-TD": "fanuc_turn",
    "Токарный станок с ЧПУ Sinumerik 802D": "sinumerik_turn",
}
SPECS = [yaml.safe_load(p.read_text("utf-8")) for p in sorted(SEED_DIR.glob("*.yaml"))]


@pytest.mark.parametrize("spec", SPECS, ids=[s["title"] for s in SPECS])
def test_split_reproduces_seed_markup(spec):
    """Нарезка собранной программы совпадает с ручной разметкой в YAML."""
    text, expected = assemble(spec)
    lines = text.splitlines()
    result = split_program(text, MACHINE_DIALECTS[spec["machine"]])

    assert [(o.op_type, o.line_start, o.line_end) for o in result.operations] == [
        (e.op_type, e.line_start, e.line_end) for e in expected
    ]
    header_lines = len(spec["header"].splitlines())
    assert result.header == (1, header_lines)
    footer_lines = len(spec["footer"].splitlines())
    assert result.footer == (len(lines) - footer_lines + 1, len(lines))
    assert all(not o.warnings for o in result.operations)


def test_program_without_tool_call_is_one_operation():
    text = "G21 G40\nG0 X50 Z2\nG1 Z-20 F0.2\nM5\nM30"
    result = split_program(text, "fanuc_turn")
    assert len(result.operations) == 1
    assert result.operations[0].op_type == "turning"
    assert result.operations[0].line_start == 1
    assert any("нет вызова инструмента" in w for w in result.warnings)
    assert result.footer == (4, 5)


def test_tool_call_before_first_cut_does_not_split_operation():
    text = "T0101\nT0101\nG0 X50 Z2\nG1 X-1 F0.2\nM30"
    result = split_program(text, "fanuc_turn")
    assert [(o.op_type, o.line_start, o.line_end) for o in result.operations] == [("facing", 1, 4)]


def test_same_tool_after_cutting_starts_new_operation():
    text = "T0101\nG0 X50 Z2\nG1 X-1 F0.2\nT0101\nG0 X40 Z2\nG1 Z-30 F0.2\nM30"
    result = split_program(text, "fanuc_turn")
    assert [(o.op_type, o.line_start, o.line_end) for o in result.operations] == [
        ("facing", 1, 3),
        ("turning", 4, 6),
    ]


def test_segment_with_two_types_gets_warning():
    text = "T0101\nG71 U2 R0.3\nG71 P10 Q20 U0.5 F0.15\nN10 G1 X20\nN20 G1 Z-10\nG76 P030060 Q300 R0.8\nG76 X18 Z-20 P800 Q300 F1.5\nM30"
    result = split_program(text, "fanuc_turn")
    operation = result.operations[0]
    assert operation.op_type == "threading"  # приоритет у резьбы
    assert any("нескольких типов" in w for w in operation.warnings)


def test_unknown_type_is_reported():
    result = split_program("T0101\nG0 X50 Z2\nM30", "fanuc_turn")
    assert result.operations[0].op_type is None
    assert any("определить не удалось" in w for w in result.operations[0].warnings)


def test_template_description_contains_numbers():
    spec = next(s for s in SPECS if s["title"].startswith("Втулка с центральным"))
    text, operations = assemble(spec)
    result = split_program(text, "fanuc_turn")
    drilling = next(o for o in result.operations if o.op_type == "drilling")
    description = template_description("drilling", drilling.features)
    assert description.startswith("Сверление")
    assert "T1111" in description and "G83" in description
    assert "частота вращения 600 об/мин" in description and "подача 0.1" in description
