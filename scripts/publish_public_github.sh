#!/usr/bin/env bash
# publish_public_github.sh — курируемая публикация тега atlas в ПУБЛИЧНЫЙ github.
#
# Зачем: gitlab (приватный) = источник правды со ВСЕМ (включая внутренние
# _project/research/docs/design/docs/superpowers — там PM-инфа, IP серверов,
# дизайн-доки). В публичный github должно уходить ТОЛЬКО нужное для (а) pip-install
# CLI `atlas` из тега и (б) чтения навыка skillery: код + skills/atlas + мета.
#
# Полное GitLab push-mirror НЕ умеет фильтровать пути → его надо ВЫКЛЮЧИТЬ
# (Settings → Repository → Mirroring), а доставку в github делать этим скриптом.
#
# Модель: держим постоянный локальный клон github (PUB_DIR). На каждый релиз:
# берём дерево тега из исходного репо (git archive), выкидываем внутренние пути,
# коммитим в master клона и пушим + пушим тег. История github = связные
# курированные снимки релизов. skillery по webhook подтянет новый тег.
#
# Использование:
#   scripts/publish_public_github.sh v0.3.0
# Тег уже должен существовать локально (git tag v0.3.0) и в исходном gitlab-репо.
set -euo pipefail

TAG="${1:-}"
if [[ -z "$TAG" ]]; then echo "usage: $0 vX.Y.Z" >&2; exit 2; fi
if ! [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "тег должен быть семвером vX.Y.Z, получено: $TAG" >&2; exit 2; fi

SRC_REPO="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"
GH_URL="${ATLAS_PUBLIC_GH_URL:-https://github.com/zZZTeJleTTy3uKZZz/atlas.git}"
PUB_DIR="${ATLAS_PUBLIC_MIRROR_DIR:-$HOME/.atlas-public-mirror}"

# Пути, которые НЕ уходят в публичный github (внутреннее).
EXCLUDES=(
  "_project"
  "research"
  "docs/design"
  "docs/superpowers"
  "docs/ATLAS_OVERVIEW.md"   # внутренний дизайн-док (прод-синк Б24/Notion)
  "docs/PRD-MVP.md"          # внутренний PRD
  "AGENTS.md"                # личный owner/бренд/пути/NotebookLM-UUID — НЕ публично
  "scripts/backup"           # machine-specific личные пути + имя клиента
  "scripts/triage"           # личные триаж-скрипты
  ".gitlab-ci.yml"           # gitlab-специфично, публично не нужно
  ".skillgateignore"
)

if ! git -C "$SRC_REPO" rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "тег $TAG не найден в $SRC_REPO — сначала создай и запушь его в gitlab" >&2
  exit 1
fi

# 1) Постоянный клон github (первый раз — клонируем).
if [[ ! -d "$PUB_DIR/.git" ]]; then
  echo "→ клонирую публичный github в $PUB_DIR"
  git clone "$GH_URL" "$PUB_DIR"
fi
cd "$PUB_DIR"
git fetch origin --prune
git checkout -B master origin/master 2>/dev/null || git checkout -B master

# 2) Чистим рабочее дерево клона (кроме .git) и раскладываем дерево тега.
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
git -C "$SRC_REPO" archive "$TAG" | tar -x -C "$PUB_DIR"

# 3) Выкидываем внутренние пути.
for p in "${EXCLUDES[@]}"; do rm -rf "$PUB_DIR/$p"; done

# 4) Коммит + тег + пуш.
git add -A
if git diff --cached --quiet; then
  echo "→ нет изменений содержимого относительно master (только тег)."
else
  git commit -q -m "atlas $TAG — публичный курируемый снимок (без внутренних путей)"
fi
git tag -f "$TAG"
git push origin master
git push -f origin "refs/tags/$TAG"
echo "✓ опубликовано в github: master + $TAG (skillery подтянет по webhook)"
