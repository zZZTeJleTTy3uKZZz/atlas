"""Движок поставляемых РЕСУРСОВ агента: субагенты, команды, правила.

Три поверхности из аудита оказались одной формой — каталог под ``config_dir``,
файл на единицу, — поэтому здесь один движок, а не три почти одинаковых.
Разница между видами выражена данными (``ResourceLayout``), и это не экономия
строк: три копии одной логики означают три места, где при первой же правке
разойдётся поведение.

Что движок делает и чего не делает:

* **делает** — говорит, куда класть и что уже лежит, кладёт и снимает файл;
* **не делает** — не решает, ЧТО внутри файла. Содержимое субагента, команды
  или правила принадлежит тому, кто их пишет.

Главная тонкость — форма файла. У Claude субагент это ``.md``, у Codex —
``.toml``, у Antigravity — каталог ``<имя>/agent.md``. Подставить своё
расширение нельзя: агент читает каталог по маске и чужой файл просто не
заметит, молча и без единой жалобы. Поэтому форма — данные реестра, а не
константа кода.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .hooks import _spec_of
from .spec import AgentSpec, ResourceLayout

#: Виды ресурсов, которые кит знает. Незнакомый вид — отказ, а не догадка.
KINDS = ("agents", "commands", "rules")


@dataclass(frozen=True)
class ResourceResult:
    """Что произошло — в словах остальных движков кита."""

    agent: str
    kind: str
    action: str  # "installed" | "removed" | "unchanged" | "skipped" | "failed"
    reason: str = ""
    path: str | None = None

    @property
    def ok(self) -> bool:
        return self.action in ("installed", "removed", "unchanged", "skipped")


def resource_layouts_for(
    agent: str | AgentSpec, kind: str | None = None
) -> tuple[ResourceLayout, ...]:
    """Каталоги ресурсов агента. Пусто — агент таких поверхностей не имеет."""
    layouts = _spec_of(agent).resources
    if kind is None:
        return layouts
    return tuple(r for r in layouts if r.kind == kind)


def resource_dir(
    agent: str | AgentSpec,
    kind: str,
    *,
    home: Path | None = None,
    project: Path | None = None,
    scope: str = "global",
) -> Path | None:
    """Каталог вида ресурса. ``None`` — агент его не держит в этом скоупе.

    ``scope`` разделён намеренно: у большинства агентов ресурс живёт и под
    ``$HOME``, и в проекте, и это РАЗНЫЕ каталоги. Сложить их в один «первый
    подходящий» — тот же класс ошибки, что уже ловил кит на раскладках
    навыков: рапорт об успехе туда, куда агент не смотрит.
    """
    spec = _spec_of(agent)
    for layout in spec.resources:
        if layout.kind != kind or scope not in layout.scopes:
            continue
        base = home if scope == "global" else project
        if base is None:
            return None
        return base.joinpath(spec.config_dir, *layout.subdir)
    return None


def unit_path(
    agent: str | AgentSpec,
    kind: str,
    name: str,
    *,
    home: Path | None = None,
    project: Path | None = None,
    scope: str = "global",
) -> Path | None:
    """Путь ОДНОЙ единицы: файла или каталога с файлом внутри."""
    spec = _spec_of(agent)
    directory = resource_dir(agent, kind, home=home, project=project, scope=scope)
    if directory is None:
        return None
    layout = next((r for r in spec.resources if r.kind == kind), None)
    if layout is None:
        return None
    if layout.per_unit_dir:
        return directory / name / (layout.filename or "agent.md")
    return directory / f"{name}{layout.suffix}"


def list_units(
    agent: str | AgentSpec,
    kind: str,
    *,
    home: Path | None = None,
    project: Path | None = None,
    scope: str = "global",
) -> list[str]:
    """Имена уже поставленных единиц. Пусто — каталога нет либо он пуст."""
    spec = _spec_of(agent)
    directory = resource_dir(agent, kind, home=home, project=project, scope=scope)
    if directory is None or not directory.is_dir():
        return []
    layout = next((r for r in spec.resources if r.kind == kind), None)
    if layout is None:
        return []
    if layout.per_unit_dir:
        имя_файла = layout.filename or "agent.md"
        return sorted(p.name for p in directory.iterdir() if (p / имя_файла).is_file())
    return sorted(
        p.stem for p in directory.iterdir()
        if p.is_file() and p.suffix == layout.suffix
    )


def install_unit(
    agent: str | AgentSpec,
    kind: str,
    name: str,
    content: str,
    *,
    home: Path | None = None,
    project: Path | None = None,
    scope: str = "global",
) -> ResourceResult:
    """Положить единицу. Повтор с тем же содержимым — ``unchanged``.

    В устаревшую поверхность не ставим: агент её ещё читает, но объявил
    уходящей, и класть туда новое — заводить работу, которую придётся
    переносить.
    """
    spec = _spec_of(agent)
    if kind not in KINDS:
        return ResourceResult(spec.key, kind, "failed",
                              f"незнакомый вид ресурса {kind!r} — известны: {', '.join(KINDS)}")
    layout = next((r for r in spec.resources if r.kind == kind), None)
    if layout is None:
        return ResourceResult(spec.key, kind, "skipped", "агент не держит такой ресурс")
    if layout.deprecated:
        return ResourceResult(spec.key, kind, "skipped",
                              "поверхность объявлена устаревшей — новое туда не кладём")

    path = unit_path(agent, kind, name, home=home, project=project, scope=scope)
    if path is None:
        return ResourceResult(spec.key, kind, "skipped", f"нет скоупа {scope!r}")
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return ResourceResult(spec.key, kind, "unchanged", "содержимое совпадает", str(path))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return ResourceResult(spec.key, kind, "installed", "", str(path))


def remove_unit(
    agent: str | AgentSpec,
    kind: str,
    name: str,
    *,
    home: Path | None = None,
    project: Path | None = None,
    scope: str = "global",
) -> ResourceResult:
    """Снять единицу. Нет её — ``unchanged``, а не ошибка."""
    spec = _spec_of(agent)
    path = unit_path(agent, kind, name, home=home, project=project, scope=scope)
    if path is None:
        return ResourceResult(spec.key, kind, "skipped", "агент не держит такой ресурс")
    if not path.exists():
        return ResourceResult(spec.key, kind, "unchanged", "уже снят", str(path))

    layout = next((r for r in spec.resources if r.kind == kind), None)
    path.unlink()
    # каталог на единицу убираем только если он опустел: рядом мог лежать
    # чужой файл, который положил человек, и уносить его молча нельзя
    if layout is not None and layout.per_unit_dir:
        parent = path.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    return ResourceResult(spec.key, kind, "removed", "", str(path))


__all__ = [
    "KINDS",
    "ResourceResult",
    "install_unit",
    "list_units",
    "remove_unit",
    "resource_dir",
    "resource_layouts_for",
    "unit_path",
]
