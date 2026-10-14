import asyncio
from pathlib import Path

import pytest
import yaml

from seed.ingest import build_draft, clean_description, load_machine, render_draft
from seed.seed import assemble

SEED_DIR = Path(__file__).parent.parent / "seed" / "programs"
SPECS = [yaml.safe_load(p.read_text("utf-8")) for p in sorted(SEED_DIR.glob("*.yaml"))]


@pytest.mark.parametrize("spec", SPECS, ids=[s["title"] for s in SPECS])
def test_draft_loads_back_to_same_program(spec):
    """Ингестия собранной программы даёт YAML, из которого seed.seed получит тот же текст и разметку."""
    text, expected = assemble(spec)
    machine = load_machine(spec["machine"])
    draft, notes = asyncio.run(build_draft(text, machine, spec["material"], spec["title"], "template", None))

    loaded = yaml.safe_load(render_draft(draft))
    again_text, again_ops = assemble(loaded)

    assert again_text == text
    assert [(o.op_type, o.line_start, o.line_end) for o in again_ops] == [
        (o.op_type, o.line_start, o.line_end) for o in expected
    ]
    assert all(o["description"] for o in loaded["operations"])
    assert notes == []


def test_unknown_operation_type_requires_explicit_types():
    machine = load_machine("Токарный станок с ЧПУ Fanuc 0i-TD")
    text = "T0101\nG0 X50 Z2\nM30"
    with pytest.raises(SystemExit, match="--types"):
        asyncio.run(build_draft(text, machine, "steel", "t", "template", None))
    draft, _ = asyncio.run(build_draft(text, machine, "steel", "t", "template", ["facing"]))
    assert draft["operations"][0]["type"] == "facing"


def test_clean_description():
    assert clean_description("```\nПодрезка торца\n  Ø60\n```") == "Подрезка торца Ø60"
