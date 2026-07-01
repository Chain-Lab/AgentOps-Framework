"""PlatformRegistry — loads PlatformSpec objects from a directory of YAML files."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from book_publisher.models import PlatformSpec


class PlatformRegistry:
    """Directory-scanned registry of downstream publishing platforms.

    Adding a new platform is adding one YAML file to the directory passed
    to :meth:`load` — it auto-registers as a governed publish tool once
    ``build_publish_tools`` runs over the registry (see tools.py).
    """

    def __init__(self) -> None:
        self._platforms: dict[str, PlatformSpec] = {}

    @classmethod
    def load(cls, dir_path: str | Path) -> PlatformRegistry:
        registry = cls()
        for path in sorted(Path(dir_path).glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            try:
                platform = PlatformSpec.model_validate(data)
            except ValidationError as exc:
                raise ValueError(f"Invalid platform file '{path}': {exc}") from exc
            if platform.name in registry._platforms:
                raise ValueError(
                    f"Duplicate platform name '{platform.name}' found in {path}"
                )
            registry._platforms[platform.name] = platform
        return registry

    def all(self) -> list[PlatformSpec]:
        return list(self._platforms.values())

    def get(self, name: str) -> PlatformSpec:
        try:
            return self._platforms[name]
        except KeyError:
            raise KeyError(
                f"'{name}' is not a registered platform. "
                f"Registered platforms: {sorted(self._platforms)}"
            ) from None

    def __len__(self) -> int:
        return len(self._platforms)
