# Установка atlas

CLI-команда — `atlas`; PyPI-пакет — `atlas-pm`. Требуется Python ≥ 3.11 (ставится вместе с `uv`, если его нет).

## Одной командой (ставит `uv` при необходимости)

**Linux / macOS:**
```bash
curl -fsSL https://raw.githubusercontent.com/zZZTeJleTTy3uKZZz/atlas/master/install/install.sh | sh
```

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/zZZTeJleTTy3uKZZz/atlas/master/install/install.ps1 | iex
```

Скрипт ставит `uv` (Astral), если его нет, затем `uv tool install atlas-pm` (изолированное окружение, свой Python) и в конце запускает **`atlas setup`** — прописывает правила Atlas в агентские файлы (CLAUDE.md/AGENTS.md) и ставит **SessionStart-хук** (сводка состояния портфеля впрыскивается в начало каждой сессии агента — заметно бустит триггеринг задач). Скрипты намеренно **ASCII-only** — иначе `irm | iex` / `| sh` могут испортить не-ASCII символы.

> Если `raw.githubusercontent.com` не резолвится / недоступен (корпоративный фаервол, РФ-провайдер без VPN) — сам скрипт не скачается, хотя ставится он с PyPI. В этом случае ставь напрямую через `uv` (см. «uv / pipx» ниже) или включи VPN.

## Другие способы

- **skillery** — ставит CLI и заодно навык для Claude-агента:
  ```bash
  skillery install atlas
  ```
- **uv / pipx** напрямую — тянет только с PyPI, **без GitHub** (годится, когда `raw.githubusercontent.com` недоступен):

  Сначала нужен **uv** (если ещё не установлен):
  ```bash
  # Linux / macOS
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Windows (PowerShell)
  irm https://astral.sh/uv/install.ps1 | iex
  ```
  Затем ставим atlas с PyPI и делаем onboarding в агента:
  ```bash
  uv tool install atlas-pm      # или (нужен Python ≥3.11): pipx install atlas-pm
  atlas setup                   # правила Atlas в агентские файлы + SessionStart-хук
  ```
- **Из исходников** (разработка / editable):
  ```bash
  git clone https://github.com/zZZTeJleTTy3uKZZz/atlas.git
  cd atlas && uv tool install --editable .
  ```

## Первый запуск

```bash
atlas config set owner <you>   # владелец стора (актор аудита, владелец новых проектов)
atlas setup                    # правила Atlas в агентов (CLAUDE.md/AGENTS.md) + SessionStart-хук триажа
atlas project init             # применить миграции БД + справочники
```

`atlas setup` — идемпотентный turnkey-онбординг в агента (можно перезапускать):

- **правила** — Atlas-дисциплина managed-блоком в агентские файлы (как `atlas init`; `--scope global|repo|all`, `--agents claude,gemini,…`);
- **хук** (Claude Code) — SessionStart-триаж: `atlas task triage` впрыскивает компактную сводку портфеля (числа + задачи в работе + забытые) в начало сессии, чтобы состояние задач было первым, что видит агент. Мёрж `~/.claude/settings.json` **не трогает чужие хуки**.

Частично / снять: `atlas setup --no-hooks` (только правила) · `--no-rules` (только хук) · `--uninstall` (снять хук) · `--dry-run`.

## Обновление

```bash
atlas update           # self-update с PyPI (детектит uv/pipx/pip)
atlas update --check   # только показать текущую/доступную версию
```
