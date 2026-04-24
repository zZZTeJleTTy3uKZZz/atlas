# ADR-001: Archive Layout — один _Archive/ с подгруппами по типу проекта

**Date**: 2026-04-24
**Status**: Accepted
**Decision-maker**: Дмитрий (owner), Claude Code (implementer)

## Context

При физической организации проектов `PROJECT/` встаёт вопрос — где хранить архивные (неактивные) проекты? Три варианта обсуждались:

A) Один flat `_Archive/` в корне — все архивные вперемешку.
B) Свой `_archive/` в каждой группе (`Clients/_archive/`, `Products/_archive/`, `Tests/_archive/`) — разделённые архивы.
C) Один `_Archive/` в корне с подгруппами `{clients, products, tests}/` внутри — централизованно но с сохранением информации о типе.

Параллельно — логические статусы проекта внутри архива: не просто «архив», а «completed» / «paused» / «frozen» / «archived» для разных причин неактивности.

## Decision

Выбран **Вариант C** (централизованный _Archive/ с подгруппами) + четыре логических статуса + поле `archived_group` в БД.

Причины:
1. **Бэкап/ревизия** — одна папка для всего архива, удобно zip'ать и просматривать.
2. **История типа** — при unarchive мы точно знаем куда возвращать (есть `archived_group` в БД и/или сохранено в пути `_Archive/<group>/`).
3. **Разные причины неактивности** имеют разные будущие действия:
   - `completed` → обычно не возвращаемся, но для клиентов → потенциал для `renew`.
   - `paused` → высокая вероятность возврата, сохраняем state максимально точно.
   - `frozen` → низкая вероятность возврата, но может разморозиться.
   - `archived` → не вернёмся никогда (history only).
4. **Метрики**:
   - `projects.renewal_count` — сколько раз клиент возвращался (индикатор здоровья отношений).
   - Время в каждом статусе через `action_log` — decay паттерны.

## Consequences

### Positive
- Понятная физика: видно по пути `_Archive/clients/<slug>` что это был клиент.
- Unarchive автоматизируется через `archived_group` — atlas знает куда возвращать.
- Метрики по клиентам (renewals) — ценная дата-точка для продуктовых решений.
- Гибкость: owner/stack/domain меняются без физических move'ов (через теги).

### Negative
- Чуть больше кода в CLI archive/unarchive (нужно ветвление по archived_group).
- При ручном перемещении проекта в обход `atlas projects archive` — рассинхрон физики и БД. Митигация: команда `atlas projects reorganize --dry-run` регулярно.

### Neutral
- `archived_group` поле избыточно к `project_type` в 99% случаев, НО если проект меняет type во время архивации — мы сохраним исходную группу. 100 байт/проект — разумная страховка.

## Related
- Миграция 004: `tags` + `project_tags` + `projects.renewal_count` + `projects.archived_group`.
- CLI: `atlas projects archive/unarchive/renew/move/reorganize` (Cycle 4 backlog W4).
