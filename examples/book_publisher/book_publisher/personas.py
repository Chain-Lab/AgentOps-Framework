"""PersonaRegistry — loads PersonaSpec objects from a directory of YAML files."""

from __future__ import annotations

from pathlib import Path

import yaml

from book_publisher.models import PersonaSpec


class PersonaRegistry:
    """Directory-scanned registry of audience personas.

    Adding a new audience is adding one YAML file to the directory passed
    to :meth:`load` — no code changes required.
    """

    def __init__(self) -> None:
        self._personas: dict[str, PersonaSpec] = {}

    @classmethod
    def load(cls, dir_path: str | Path) -> PersonaRegistry:
        registry = cls()
        for path in sorted(Path(dir_path).glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            persona = PersonaSpec.model_validate(data)
            if persona.name in registry._personas:
                raise ValueError(
                    f"Duplicate persona name '{persona.name}' found in {path}"
                )
            registry._personas[persona.name] = persona
        return registry

    def all(self) -> list[PersonaSpec]:
        return list(self._personas.values())

    def get(self, name: str) -> PersonaSpec:
        return self._personas[name]

    def __len__(self) -> int:
        return len(self._personas)
