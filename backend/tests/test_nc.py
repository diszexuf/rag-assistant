from app import nc
from app.nc import MachineInfo

MACHINE = MachineInfo(max_spindle_rpm=3000, tool_positions=frozenset({1, 2, 4}))

FANUC_OK = """O0001 (TEST)
G21 G40 G54 G99
T0101
G50 S1600
G96 S130 M3
G0 X84 Z0 M8
G01 X-1.6 F0.15
G28 U0 W0
M30
"""

SINUMERIK_OK = """; TEST
G54 G40 G71 G90 G95
T1 D1
G96 S 110 M3
LIMS=1300
G0 X76 Z2 M8
CYCLE95("CONTUR",2,0,0,0.5,0.15,0.1,,1,0.1,20,1,0,2)
G0 X200 Z200
M30
"""


def codes(report):
    return [(issue.code, issue.line) for issue in report.issues]


def test_fanuc_parser_normalizes_commands_and_reads_block_numbers():
    program = nc.parse("N10 G01 X-1.6 F0.15 (PODREZKA)\n%\n\nN20 G00X10.Z2.", "fanuc_turn")
    first, second = program.blocks
    assert (first.line, first.number, first.commands) == (1, 10, ["G1"])
    assert first.word("X").as_float() == -1.6
    assert first.comment == "PODREZKA"
    assert (second.line, second.commands) == (4, ["G0"])
    assert second.word("Z").as_float() == 2.0


def test_sinumerik_parser_reads_calls_assignments_and_comments():
    program = nc.parse('T="REZEC 1" M6 ; smena\nLIMS=1300\nCYCLE95("K",2)\nPODPROG', "sinumerik_turn")
    tool, lims, cycle, sub = program.blocks
    assert tool.word("T").value == '"REZEC 1"' and tool.commands == ["M6"]
    assert tool.comment == "SMENA"
    assert lims.word("LIMS").as_float() == 1300
    assert cycle.commands == ["CYCLE95"]
    assert sub.unknown_names == ["PODPROG"]


def test_valid_programs_have_no_issues():
    assert codes(nc.validate(FANUC_OK, "fanuc_turn", MACHINE)) == []
    assert codes(nc.validate(SINUMERIK_OK, "sinumerik_turn", MACHINE)) == []


def test_syntax_error():
    report = nc.validate(FANUC_OK.replace("G0 X84 Z0 M8", "G0 X84 Z= M8"), "fanuc_turn", MACHINE)
    assert codes(report) == [("SYNTAX", 6)]


def test_unknown_command():
    report = nc.validate(FANUC_OK.replace("G28 U0 W0", "G128 U0 W0"), "fanuc_turn", MACHINE)
    assert codes(report) == [("UNKNOWN_COMMAND", 8)]
    # Цикл Sinumerik в программе Fanuc не разбирается вовсе
    report = nc.validate(FANUC_OK.replace("G28 U0 W0", "CYCLE95(1)"), "fanuc_turn", MACHINE)
    assert codes(report) == [("SYNTAX", 8)]


def test_no_program_end():
    report = nc.validate(FANUC_OK.replace("M30\n", ""), "fanuc_turn", MACHINE)
    assert codes(report) == [("NO_PROGRAM_END", 8)]
    assert codes(nc.validate("", "fanuc_turn")) == [("EMPTY_PROGRAM", 1)]


def test_tool_not_in_magazine():
    report = nc.validate(FANUC_OK.replace("T0101", "T0707"), "fanuc_turn", MACHINE)
    assert codes(report) == [("TOOL_NOT_IN_MAGAZINE", 3)]
    report = nc.validate(SINUMERIK_OK.replace("T1 D1", "T9 D1"), "sinumerik_turn", MACHINE)
    assert codes(report) == [("TOOL_NOT_IN_MAGAZINE", 3)]
    # Без сведений о станке проверка не выполняется
    assert codes(nc.validate(FANUC_OK.replace("T0101", "T0707"), "fanuc_turn")) == []


def test_spindle_limit():
    # При G96 значение S — скорость резания, а не обороты
    assert codes(nc.validate(FANUC_OK.replace("S130", "S5000"), "fanuc_turn", MACHINE)) == []
    report = nc.validate(FANUC_OK.replace("G96 S130", "G97 S3500"), "fanuc_turn", MACHINE)
    assert codes(report) == [("SPINDLE_LIMIT", 5)]
    report = nc.validate(FANUC_OK.replace("G50 S1600", "G50 S4000"), "fanuc_turn", MACHINE)
    assert codes(report) == [("SPINDLE_LIMIT", 4)]
    report = nc.validate(SINUMERIK_OK.replace("LIMS=1300", "LIMS=9000"), "sinumerik_turn", MACHINE)
    assert codes(report) == [("SPINDLE_LIMIT", 5)]


def test_converter_stub_reports_every_line_as_unconverted():
    result = nc.convert("G0 X10\n\nG1 Z-5", "fanuc_turn", "sinumerik_turn")
    assert [u.line for u in result.unconverted] == [1, 3]
    assert nc.convert("G0 X10", "fanuc_turn", "fanuc_turn").unconverted == []
