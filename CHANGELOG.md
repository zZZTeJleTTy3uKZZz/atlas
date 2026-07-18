# Changelog

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
