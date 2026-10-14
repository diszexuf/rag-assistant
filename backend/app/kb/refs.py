"""Фиксированные справочники ядра MVP."""

OP_TYPES: dict[str, str] = {
    "turning": "Наружное точение",
    "facing": "Подрезка торца",
    "drilling": "Сверление",
    "threading": "Нарезание резьбы",
}

MATERIALS: dict[str, str] = {
    "steel": "Конструкционная сталь",
    "stainless": "Нержавеющая сталь",
    "aluminium": "Алюминиевый сплав",
    "brass": "Латунь",
}

MACHINE_KINDS: dict[str, str] = {
    "lathe": "Токарный",
}
