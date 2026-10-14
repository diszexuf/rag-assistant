"""Ингестия УП из файла: python -m seed.ingest <файл> --machine "<станок>" --material steel

Режет программу на операции (app/kb/split.py), определяет типы, описывает операции
и записывает черновик в формате seed/programs/*.yaml в seed/programs/drafts/.
Черновик проверяет человек: границы, типы и описания. После проверки файл копируется
в seed/programs/ и загружается обычным python -m seed.seed.
"""

import argparse
import asyncio
import re
from datetime import date
from pathlib import Path

import yaml

from app import nc
from app.config import get_settings
from app.kb.refs import MATERIALS, OP_TYPES
from app.kb.split import OperationDraft, split_program, template_description
from app.llm.base import ChatMessage, ChatModel, LLMError
from app.llm.factory import get_chat_model
from app.nc import MachineInfo
from app.orchestrator.pipeline import PROMPTS_DIR

SEED_DIR = Path(__file__).parent
DRAFTS_DIR = SEED_DIR / "programs" / "drafts"
_FENCE = re.compile(r"```[a-zA-Z]*\n?|```")


class _Dumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, value: str):
    # Код программы пишем блоками, а не строкой с \n
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str_representer)


def load_machine(name: str) -> dict:
    machines = yaml.safe_load((SEED_DIR / "data" / "machines.yaml").read_text("utf-8"))
    for machine in machines:
        if machine["name"] == name:
            return machine
    available = ", ".join(m["name"] for m in machines)
    raise SystemExit(f"Станок «{name}» не найден в seed/data/machines.yaml. Есть: {available}")


def slice_lines(lines: list[str], span: tuple[int, int] | None) -> str:
    if span is None:
        return ""
    return "\n".join(line.rstrip() for line in lines[span[0] - 1 : span[1]])


def clean_description(text: str) -> str:
    text = _FENCE.sub("", text)
    return " ".join(text.split())[:600]


async def describe_with_llm(
    chat: ChatModel, machine: dict, op_type: str, code: str, dialect_title: str
) -> str:
    tools = "\n".join(
        f"- позиция {t['position']}: {t.get('name', '')} ({t.get('type', '')})" for t in machine["tools"]
    )
    template = (PROMPTS_DIR / "describe.md").read_text(encoding="utf-8")
    fields = {
        "machine_name": machine["name"],
        "dialect_title": dialect_title,
        "op_type": OP_TYPES[op_type],
        "tools": tools,
        "code": code,
    }
    for name, value in fields.items():
        template = template.replace("{{" + name + "}}", value)
    answer = await chat.complete([ChatMessage("user", template)])
    return clean_description(answer)


async def build_draft(
    text: str,
    machine: dict,
    material: str,
    title: str,
    describe: str,
    types: list[str] | None,
) -> tuple[dict, list[str]]:
    dialect = machine["dialect"]
    spec = nc.load_dialect(dialect)
    lines = text.splitlines()
    split = split_program(text, dialect)
    notes: list[str] = list(split.warnings)

    if types is not None:
        if len(types) != len(split.operations):
            raise SystemExit(
                f"--types: указано {len(types)} типов, а в программе найдено операций: {len(split.operations)}"
            )
        for operation, op_type in zip(split.operations, types):
            operation.op_type = op_type

    chat = get_chat_model() if describe == "llm" else None
    operations = []
    for index, operation in enumerate(split.operations, start=1):
        operation_notes = [f"Операция {index}: {w}" for w in operation.warnings]
        if operation.op_type is None:
            raise SystemExit(
                f"Операция {index} (строки {operation.line_start}–{operation.line_end}): "
                "тип определить не удалось. Укажите типы флагом --types, например "
                "--types facing,turning,drilling"
            )
        notes.extend(operation_notes)
        code = slice_lines(lines, (operation.line_start, operation.line_end))
        description = await _describe(chat, machine, operation, code, spec.title, notes, index)
        operations.append({"type": operation.op_type, "description": description, "code": code + "\n"})

    draft = {
        "title": title,
        "machine": machine["name"],
        "material": material,
        "header": slice_lines(lines, split.header) + "\n" if split.header else "",
        "operations": operations,
        "footer": slice_lines(lines, split.footer) + "\n" if split.footer else "",
    }
    return draft, notes


async def _describe(
    chat: ChatModel | None,
    machine: dict,
    operation: OperationDraft,
    code: str,
    dialect_title: str,
    notes: list[str],
    index: int,
) -> str:
    fallback = template_description(operation.op_type, operation.features)
    if chat is None:
        return fallback
    try:
        description = await describe_with_llm(chat, machine, operation.op_type, code, dialect_title)
    except LLMError as exc:
        notes.append(f"Операция {index}: описание по модели не получено ({exc}), взят шаблон")
        return fallback
    if not description:
        notes.append(f"Операция {index}: модель вернула пустое описание, взят шаблон")
        return fallback
    return description


def render_draft(draft: dict) -> str:
    header = (
        f"# Черновик ингестии от {date.today().isoformat()}.\n"
        "# Проверьте границы операций, типы и описания, затем перенесите файл в seed/programs/.\n"
    )
    return header + yaml.dump(draft, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=100)


async def main(args: argparse.Namespace) -> None:
    source = Path(args.file)
    text = source.read_text(encoding="utf-8")
    machine = load_machine(args.machine)
    if args.material not in MATERIALS:
        raise SystemExit(f"Материал {args.material!r} неизвестен. Допустимо: {', '.join(MATERIALS)}")
    types = args.types.split(",") if args.types else None
    if types and any(t not in OP_TYPES for t in types):
        raise SystemExit(f"Неизвестный тип операции в --types. Допустимо: {', '.join(OP_TYPES)}")

    describe = args.describe
    if describe == "auto":
        describe = "llm" if get_settings().llm_provider != "mock" else "template"

    title = args.title or source.stem
    draft, notes = await build_draft(text, machine, args.material, title, describe, types)

    info = MachineInfo(
        max_spindle_rpm=machine["max_spindle_rpm"],
        tool_positions=frozenset(int(t["position"]) for t in machine["tools"]),
    )
    report = nc.validate(text, machine["dialect"], info)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{source.stem}.yaml"
    path.write_text(render_draft(draft), encoding="utf-8")

    print(f"Операций: {len(draft['operations'])}, описания: {describe}")
    for number, operation in enumerate(draft["operations"], start=1):
        print(f"  {number}. [{operation['type']}] {operation['description']}")
    for note in notes:
        print(f"  ! {note}")
    for issue in report.issues:
        print(f"  ! строка {issue.line} [{issue.severity}] {issue.message}")
    print(f"Черновик: {path}")
    print("Проверьте его и перенесите в seed/programs/, затем: python -m seed.seed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", help="текстовый файл УП")
    parser.add_argument("--machine", required=True, help="название станка из seed/data/machines.yaml")
    parser.add_argument("--material", required=True, help="steel, stainless, aluminium, brass")
    parser.add_argument("--title", default="", help="заголовок (по умолчанию имя файла)")
    parser.add_argument(
        "--describe",
        choices=["auto", "llm", "template"],
        default="auto",
        help="auto: по модели, если LLM_PROVIDER не mock; template: по признакам кода",
    )
    parser.add_argument("--types", default="", help="типы операций по порядку: facing,turning,...")
    parser.add_argument("--out", default=str(DRAFTS_DIR), help="папка для черновика")
    asyncio.run(main(parser.parse_args()))
