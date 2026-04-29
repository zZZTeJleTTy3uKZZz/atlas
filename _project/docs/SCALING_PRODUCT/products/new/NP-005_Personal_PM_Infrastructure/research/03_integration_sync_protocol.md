# Integration Sync Protocol (Step 3 — Focused Follow-up)

**Source**: ask-ответ блокнота `0c2805ab-42f8-4e98-86c7-e7a618f0f850`
**Saved as note**: `Integration Sync Protocol (Step 3) (02c72299...)`
**Date**: 2026-04-22

Sync-протокол для одиночки на 10 клиентов: Bitrix24 + Notion + local markdown + memory.

---

## 1. SSOT-карта

**Главное правило:** отказ от полной двусторонней синхронизации одних и тех же полей, чтобы избежать infinite loops [3, 4].

| Сущность | Канон | Зеркало | Направление | Частота | Обоснование |
|---|---|---|---|---|---|
| `client_company` | Bitrix24 | Notion Clients | B24 → Notion | Event | CRM — система коммуникации |
| `client_contact` | Bitrix24 | Notion Clients | B24 → Notion | Event | CRM-природа |
| `deal` (сделка/проект) | Bitrix24 | Notion DS_PROJECTS | B24 → Notion | Event | — |
| `client_task` (SLA) | Bitrix24 | Notion Tasks | B24 → Notion | Event | Клиент живёт в B24, запрос неизменен |
| `dev_task` (инженерный) | Notion Tasks | Локальный Git BACKLOG.md | Notion → Git | Daily / Manual через `notion-task-cli` | Декомпозиция запроса в Notion |
| `project_page` (wiki) | Notion | — | — | — | В B24 сложно вести структурированную Wiki |
| `code_artifact` | Локальный Git | — | — | — | Без sync в B24/Notion (только ссылки в Markdown) |
| `research_finding` | NotebookLM | Локальный `research/` | NotebookLM → Git | Manual | — |

---

## 2. Топ-5 TRIGGERS для Latenode

Latenode идеален для соло: тарифицирует CPU time (JS-ноды), не каждое действие (в отличие от Zapier) [2, 5].

### 1. Создание/обновление сделки в B24 → Проект в Notion
- **B24 Webhook:** `ONCRMDEALADD` / `ONCRMDEALUPDATE`
- **Latenode JS:** парсит payload, ищет проект в Notion по `b24_deal_id`.
- **Notion API:** `Find or Create Database Item` [6]. Если создаётся новый — прописывает `b24_company_id`.

### 2. Новый комментарий клиента в задаче B24 → Лог в Notion
- **B24 Webhook:** `ONTASKCOMMENTADD`
- **Notion API Action:** `Add Block to Page` [7]. Append-only в конец страницы задачи в Notion.

### 3. Закрытие dev_task в Notion → Обновление B24
- **Trigger:** Notion Webhook / Polling (Latenode).
- **Action:** Если Status = "Done" в Notion → `task.item.update` в B24 (Status = "Ожидает контроля" + комментарий "Готово").

### 4. Drift-мониторинг (self-built Python)
- **Trigger:** `notion-task-cli` ежедневно проверяет `openapi.yaml` клиентского B24 на изменения.
- **Action:** diff найден → создаёт `dev_task` в Notion: "Внимание: Schema Drift в B24 клиента X".

### 5. B24 CoPilot summary → Notion
- B24 CoPilot делает саммари звонка [8, 9].
- Latenode забирает саммари → `Add Block to Page` в проектную страницу Notion.

---

## 3. Conflict Resolution — canonical-field-per-concept

Применяем **canonical-field-per-concept** (у каждого понятия — только один источник правды), а не "last-write-wins" [3, 4].

### Status (никогда не связывать статусы напрямую!)
- В Notion два поля:
  - `Client_B24_Status` — read-only, обновляется из B24.
  - `My_Dev_Status` — управляется вами.
- Latenode: только когда `My_Dev_Status = Done` → триггер переводит B24 в "Выполнено".

### Due Date
- **Канон:** Bitrix24.
- Клиент сдвинул сроки → Latenode обновляет `B24_Deadline` в Notion.
- Вы не редактируете сроки клиента из Notion (защита от случайных сдвигов в CRM).

### Descriptions
- **Канон:** Bitrix24 (одноразово при создании).
- Все дальнейшие уточнения → в отдельное поле `Dev_Notes` в Notion.

---

## 4. KV-CACHE-STABLE Memory Pattern (Append-Only)

Перезапись исторических данных (Context Rot) уничтожает KV-cache в LLM, увеличивая стоимость токенов в 10× и снижая точность [10, 11].

### Правила

1. **Строгий Append-Only.** Никогда не редактировать старые записи, решения или закрытые чек-листы задним числом.
2. **Мутация через новые блоки.** Если требование изменилось — не стирать старое. Добавить в низ `ACTION_LOG.md` (или через `Add Block to Page` в Notion [7]):
   ```
   ### [2026-04-22] UPDATE: Требование изменено клиентом.
   Предыдущий блок <link> неактуален.
   ```
3. **Что НЕ редактировать:** транскрипты встреч, логи API-ошибок, принятые решения (ADR).
4. **Что можно редактировать (metadata):** properties карточки в Notion БД (теги, статусы, assignees). Контент страницы — только растёт вниз.

---

## 5. BACK-LINKS: YAML frontmatter для AGENTS.md

`AGENTS.md` **обязан** содержать ключи связи с Notion и B24 — основа machine-readable sovereignty [12, 13], чтобы Claude Code при запуске в папке клиента мгновенно знал API-эндпоинты.

### Минимальный frontmatter (≤ 15 строк)

```yaml
---
type: "client_project"
client_code: "PRAGMAT_01"
b24_company_id: "784"
notion_project_id: "a1b2c3d4-e5f6-7890-1234-56789abcdef0"
b24_webhook_url: "{{env.B24_WH_URL_PRAGMAT}}"  # не хардкодить секреты!
context_rules:
  - "Do NOT modify B24 deal stages directly via API."
  - "Append logs to Notion page using notion-task-cli."
---
```

### Автоматическое поддержание актуальности — idempotent script

1. Добавить команду в `notion-task-cli`: `notion-task-cli sync-agents --project PRAGMAT_01`.
2. Скрипт стучится в Notion, забирает `b24_company_id` и `notion_project_id`.
3. Парсит `AGENTS.md` (через `python-frontmatter`).
4. ID не совпадают или пусты → перезаписывает блок frontmatter и git commit.
5. Запускается через **pre-commit hook** — не запушим код, если ID рассинхронизировались с SSOT.
