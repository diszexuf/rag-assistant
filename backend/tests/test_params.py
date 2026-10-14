from pathlib import Path

import yaml

from app.orchestrator.params import extract_values, missing_value_issues
from seed.seed import assemble

SEED_DIR = Path(__file__).parent.parent / "seed" / "programs"
MACHINE_DIALECTS = {
    "Токарный станок с ЧПУ Fanuc 0i-TD": "fanuc_turn",
    "Токарный станок с ЧПУ Sinumerik 802D": "sinumerik_turn",
}


def kinds(op_type: str, description: str) -> dict[str, float]:
    return {v.kind: v.value for v in extract_values(op_type, description, 1)}


def test_extract_facing():
    found = kinds(
        "facing",
        "Подрезка торца заготовки диаметром 60 мм резцом T0101, скорость резания 120 м/мин, подача 0,15 мм/об",
    )
    assert found == {"diameter": 60, "speed": 120, "feed": 0.15, "tool": 101}
    assert kinds("facing", "Торцевание заготовки Ø52, скорость 250")["diameter"] == 52


def test_extract_turning_drilling_threading():
    turning = kinds(
        "turning",
        "Черновое точение от диаметра 82 мм до 45 мм, длина 100 мм, глубина резания 2 мм, припуск 0.5 мм",
    )
    assert turning["final_diameter"] == 45 and turning["length"] == 100
    assert turning["cut_depth"] == 2 and turning["allowance"] == 0.5

    # Глубина резания не принимается за глубину сверления
    drilling = kinds("drilling", "Сверление диаметром 20 мм на глубину 42 мм, 600 об/мин, подача 0.1")
    assert drilling["depth"] == 42 and drilling["rpm"] == 600 and "cut_depth" not in drilling
    assert "depth" not in kinds("drilling", "Сверление, глубина резания 2 мм")

    threading = kinds("threading", "Нарезание резьбы М45х1.5 длиной 26 мм резцом T0404, 750 об/мин")
    assert threading["pitch"] == 1.5 and threading["length"] == 26 and threading["tool"] == 404


def test_instruction_extracts_only_general_kinds():
    found = kinds("", "Подачу при точении сделать 0.2 мм/об, глубина 30 мм")
    assert found == {"feed": 0.2}


def test_all_seed_operations_pass_their_own_checks():
    """Регрессия на ложные срабатывания: образцы базы знаний соответствуют своим описаниям."""
    checked = 0
    for path in sorted(SEED_DIR.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text("utf-8"))
        dialect = MACHINE_DIALECTS[spec["machine"]]
        text, operations = assemble(spec)
        lines = text.splitlines()
        for op in operations:
            fragment = "\n".join(lines[op.line_start - 1 : op.line_end])
            issues = missing_value_issues(fragment, dialect, [(op.op_type, op.description)])
            assert issues == [], f"{path.name}: {op.op_type}: {[i.message for i in issues]}"
            checked += 1
    assert checked >= 10


def test_changed_depth_gives_one_warning():
    description = "Сверление отверстия диаметром 20 мм на глубину 30 мм сверлом T1111, 600 об/мин"
    program = "T1111\nG97 S600 M3\nG0 X0 Z2\nG83 X0 Z-42 Q1000 R2 F0.1\nG80\nM30"
    issues = missing_value_issues(program, "fanuc_turn", [("drilling", description)])
    assert len(issues) == 1
    assert issues[0].code == "REQUEST_VALUE_MISSING" and issues[0].severity == "warning"
    assert issues[0].line == 0 and "30" in issues[0].message and "Операция 1" in issues[0].message


def test_tool_position_matches_fanuc_tool_with_offset():
    program = "T0101\nG97 S800 M3\nM30"
    assert missing_value_issues(program, "fanuc_turn", [("facing", "резцом T1")]) == []
    assert missing_value_issues(program, "fanuc_turn", [("facing", "резцом T0101")]) == []
    assert len(missing_value_issues(program, "fanuc_turn", [("facing", "резцом T0202")])) == 1


def test_sinumerik_cycle_arguments_are_searched():
    program = 'T1 D1\nG96 S110 M3\nCYCLE95("CONTUR",2,0,0,0.5,0.15,0.1,,1,0.1,20,1,0,2)\nM30'
    description = "Контур циклом CYCLE95, глубина резания 2 мм, припуск 0.5 мм, подача 0.15 мм/об"
    assert missing_value_issues(program, "sinumerik_turn", [("turning", description)]) == []
    wrong = description.replace("0.5 мм", "0.8 мм")
    assert len(missing_value_issues(program, "sinumerik_turn", [("turning", wrong)])) == 1


def test_facing_diameter_allows_approach_margin():
    description = "Подрезка торца заготовки диаметром 60 мм"
    assert missing_value_issues("G0 X62 Z0\nM30", "fanuc_turn", [("facing", description)]) == []
    assert len(missing_value_issues("G0 X84 Z0\nM30", "fanuc_turn", [("facing", description)])) == 1
