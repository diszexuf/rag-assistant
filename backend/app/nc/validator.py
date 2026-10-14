from app.nc.dialect import Dialect, load_dialect
from app.nc.parser import parse
from app.nc.types import Block, Issue, MachineInfo, ValidationReport


def validate(text: str, dialect: str, machine: MachineInfo | None = None) -> ValidationReport:
    spec = load_dialect(dialect)
    program = parse(text, dialect)
    report = ValidationReport()

    if not program.blocks:
        report.issues.append(Issue(1, "EMPTY_PROGRAM", "error", "Программа пуста"))
        return report

    css = False
    for block in program.blocks:
        if block.error:
            report.issues.append(
                Issue(block.line, "SYNTAX", "error", f"Синтаксическая ошибка: {block.error}")
            )
            continue
        _check_commands(block, spec, report)
        css = _check_spindle(block, spec, machine, css, report)
        _check_tool(block, spec, machine, report)

    _check_program_end(program.blocks, spec, report)
    report.issues.sort(key=lambda issue: issue.line)
    return report


def _check_commands(block: Block, spec: Dialect, report: ValidationReport) -> None:
    for command in block.commands:
        if command not in spec.commands:
            report.issues.append(
                Issue(
                    block.line,
                    "UNKNOWN_COMMAND",
                    "error",
                    f"Команда {command} отсутствует в диалекте «{spec.title}»",
                )
            )
    for name in block.unknown_names:
        report.issues.append(
            Issue(
                block.line,
                "UNKNOWN_NAME",
                "warning",
                f"Имя {name} не описано в диалекте: вызов подпрограммы или опечатка",
            )
        )


def _check_spindle(
    block: Block,
    spec: Dialect,
    machine: MachineInfo | None,
    css: bool,
    report: ValidationReport,
) -> bool:
    if spec.css_on in block.commands:
        css = True
    if spec.css_off in block.commands:
        css = False
    if machine is None or machine.max_spindle_rpm is None:
        return css
    limit = machine.max_spindle_rpm

    if spec.spindle_limit_word:
        word = block.word(spec.spindle_limit_word)
        if word and (value := word.as_float()) is not None and value > limit:
            report.issues.append(_spindle_issue(block.line, "Ограничение оборотов", value, limit))

    speed = block.word("S")
    if speed is None or (value := speed.as_float()) is None:
        return css
    if spec.spindle_limit_command and spec.spindle_limit_command in block.commands:
        if value > limit:
            report.issues.append(_spindle_issue(block.line, "Ограничение оборотов", value, limit))
    elif not css and value > limit:
        # При постоянной скорости резания S — это м/мин, а не обороты
        report.issues.append(_spindle_issue(block.line, "Частота вращения", value, limit))
    return css


def _spindle_issue(line: int, what: str, value: float, limit: int) -> Issue:
    return Issue(
        line,
        "SPINDLE_LIMIT",
        "error",
        f"{what} {value:g} об/мин превышает предел станка {limit} об/мин",
    )


def _check_tool(
    block: Block, spec: Dialect, machine: MachineInfo | None, report: ValidationReport
) -> None:
    if machine is None or machine.tool_positions is None:
        return
    word = block.word("T")
    if word is None or word.value.startswith('"'):
        return
    digits = word.value.lstrip("+-").split(".")[0]
    if not digits.isdigit():
        return
    if spec.tool_format == "fanuc_tt" and len(digits) >= 3:
        position = int(digits[:-2])
    else:
        position = int(digits)
    if position == 0:
        return
    if position not in machine.tool_positions:
        available = ", ".join(str(p) for p in sorted(machine.tool_positions))
        report.issues.append(
            Issue(
                block.line,
                "TOOL_NOT_IN_MAGAZINE",
                "error",
                f"Инструмент в позиции {position} отсутствует в магазине станка (есть: {available})",
            )
        )


def _check_program_end(blocks: list[Block], spec: Dialect, report: ValidationReport) -> None:
    last = blocks[-1]
    if not any(command in spec.program_end for command in last.commands):
        expected = ", ".join(spec.program_end)
        report.issues.append(
            Issue(
                last.line,
                "NO_PROGRAM_END",
                "error",
                f"Программа не завершена командой конца программы ({expected})",
            )
        )
