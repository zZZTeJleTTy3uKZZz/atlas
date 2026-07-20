# Changelog

## 0.3.1 — закрытие тех-долга аудита 2026-06-30 (#894)

Починены все 16 оставшихся дефектов аудита (`docs/design/2026-06-30-atlas-audit-findings.md`).

**Исправления с влиянием на данные/надёжность**
- **backup больше не пропускает проекты** [5]: гейт смотрел на legacy `git_repo_url`,
  который `git link`/`move`/`sync-from-remote` не заполняли — привязанные проекты
  молча не бэкапились. Источник правды теперь `git_remote_url` (+ зеркалирование legacy).
- **sync-демон стартовал из чужой папки** [6]: `parents[4]` (артефакт rename pm→sync)
  → поиск `pyproject.toml` вверх по дереву.
- **push учитывает неудачные попытки** [13]: `mark_failed` был мёртвым кодом; теперь
  attempts/last_error пишутся, а в `failed` запись уходит только по порогу (5) —
  одиночная сетевая ошибка не выбрасывает событие из очереди.
- **pull различает applied/skipped** [12]: пропущенные события (гонка порядка доставки)
  больше не считаются применёнными и видны в логе `watch`.
- **archive атомарнее** [17]: новый junction создаётся ДО снятия старого.

**Поведенческие изменения**
- `task reject` возвращает задачу в **todo** (было `in_progress`) [10] — `submit` снимает
  lease, а `in_progress` без lease нарушал инвариант. Брать заново — `task start`.
- `task reject` **только из `review`** [4] (принимал `blocked` в обход lease-гейта `unblock`).
- `task approve` проверяет reviewer-гейт ДО идемпотентного закрытия и не пишет
  approve-комментарий, если перехода не было [9].
- `task reopen` сбрасывает `started_at` [16] (иначе lead-time считался от первого старта).
- `project git init` **не падает на кастомном типе** [11] — fallback `products`, как в layout.
- `backlog archive` запрещён для уже `converted` идеи [19] (`--hard` по-прежнему удаляет).
- `backlog`-команды по legacy idea/inbox дают предметный маршрут в `atlas project` [8];
  `backlog add` не занимает slug legacy-записи [14]; `--project` не принимает idea/inbox [15].
- Глобальные `--json/--text` уважают POSIX-сентинел `--` [20].

## 0.3.0 — RESTful-канон срезов CLI (BREAKING)

Приведение поверхности CLI к канону `<ресурс> <глагол>` (методология kit-integration):
подчинённые ресурсы вложены в родителя (как `epic worktree` / `project git`),
плоские дефисы и дубли убраны. **Чистый разрыв — старых имён больше нет.**

### Migration (старое → новое)

| Было | Стало |
|---|---|
| `atlas member add --task <t> …` | `atlas task member add <t> …` |
| `atlas checklist add --task <t> …` | `atlas task checklist add <t> …` |
| `atlas project member-add <p> …` | `atlas project member add <p> …` |
| `atlas project member-list/-remove` | `atlas project member list / rm` |
| `atlas project add-tags / remove-tags` | `atlas project tag add / rm` |
| `atlas participant …` | `atlas person …` (домен `Participant` не меняется) |
| `atlas type edit <t> …` | `atlas type update <t> …` |
| `atlas logs …` | `atlas log list …` |
| `atlas action-log list …` | `atlas log raw …` |
| `atlas connect <url>` / `disconnect` | `atlas backend connect <url>` / `disconnect` / `status` |
| `atlas backup install / uninstall / list-tasks` | `atlas backup schedule install / uninstall / list` |
| `atlas config init` | `atlas config setup` |
| `atlas upgrade [--reinstall]` | `atlas update --from-git` |

### Не тронуто

- `idea` / `inbox` — остаются top-level: это НЕ дубли `backlog`, а отдельные
  сущности со своей материализацией (`idea promote` → layout/junction/IDEA.md/
  extract-backlog; `inbox` → физическая свалка `_Inbox/<slug>/`). Сведение в
  `backlog` = либо потеря функциональности, либо отдельная миграция — вынесено
  из этого релиза.
- `dash` — короткий алиас `dashboard` (осознанный UX).
- `sync up` — оставлен как есть (обёртка над `sync daemon install`).

### Notes

- Реестр людей переименован только на уровне CLI-фасада (`person`); доменная
  модель `Participant`/`ProjectParticipant` и `action_log entity_type="participant"`
  сохранены — рассинхрон CLI↔домен намеренный.
