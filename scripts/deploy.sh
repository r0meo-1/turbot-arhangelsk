#!/usr/bin/env bash
#
# Обновить сервер и доказать, что обновление доехало.
#
# Ручной деплой из трёх команд трижды заканчивался тем, что код не доезжал, а
# выглядело это как «бот не изменился»: вставка обрывалась, pull утыкался в
# локальные правки, сервис перезапускался со старым кодом. Все три случая
# молчали. Здесь каждый шаг сверяется с результатом, и расхождение — ошибка,
# а не строчка в выводе, которую никто не читает.
#
#   bash scripts/deploy.sh          # обновить и перезапустить оба сервиса
#   bash scripts/deploy.sh --check  # показать расхождение, ничего не меняя
#
# Через bash, а не ./: GitHub API кладёт файлы как 100644, и после git pull
# бита +x может не быть — деплой не должен спотыкаться об это первым.
#
# Тело завёрнуто в функцию не для красоты: bash дочитывает скрипт по мере
# выполнения, а этот скрипт обновляет сам себя через git pull. Функция
# заставляет разобрать файл целиком до первой команды.

set -euo pipefail

main() {
  local app_dir="${APP_DIR:-/opt/turbot}"
  local branch="${DEPLOY_BRANCH:-main}"
  local check_only=0
  [ "${1:-}" = "--check" ] && check_only=1

  cd "$app_dir"
  export GIT_PAGER=cat GIT_TERMINAL_PROMPT=0

  local before target
  before="$(git rev-parse --short HEAD)"
  git fetch --quiet origin "$branch"
  target="$(git rev-parse --short "origin/$branch")"

  echo "каталог:  $app_dir"
  echo "сейчас:   $before"
  echo "на remote: $target"

  # Грязное дерево — самая частая причина молчаливого отказа. Показать раньше,
  # чем pull скажет что-то менее внятное.
  local dirty
  dirty="$(git status --porcelain)"
  if [ -n "$dirty" ]; then
    echo
    echo "локальные изменения (могут заблокировать pull):"
    git status --short
    git diff --summary | sed 's/^/  /'
    echo
  fi

  if [ "$check_only" = "1" ]; then
    [ "$before" = "$target" ] && echo "==> обновлений нет" || echo "==> есть что обновить"
    _report_running
    return 0
  fi

  if [ "$before" != "$target" ]; then
    echo "==> git pull --ff-only"
    if ! git pull --ff-only --quiet origin "$branch"; then
      echo "ОШИБКА: pull не прошёл." >&2
      echo "Если мешают локальные правки выше: git stash — или git checkout -- <файл>." >&2
      exit 1
    fi
  fi

  local after
  after="$(git rev-parse --short HEAD)"
  if [ "$after" != "$target" ]; then
    echo "ОШИБКА: после pull HEAD=$after, ожидался $target." >&2
    exit 1
  fi
  echo "==> код на диске: $after"

  local units="turbot"
  systemctl list-unit-files vk-turbot.service >/dev/null 2>&1 && units="turbot vk-turbot"
  echo "==> перезапуск: $units"
  # shellcheck disable=SC2086
  systemctl restart $units
  sleep 8

  local failed=0
  _verify turbot    8000 "$after" || failed=1
  if [ "$units" != "turbot" ]; then
    _verify vk-turbot 5100 "$after" || failed=1
  fi

  if [ "$failed" = "1" ]; then
    echo >&2
    echo "Деплой НЕ подтверждён. Логи: journalctl -u turbot -u vk-turbot -n 40 --no-pager" >&2
    exit 1
  fi
  echo "==> готово, оба сервиса на $after"
}

_verify() {
  local unit="$1" port="$2" want="$3" body got
  body="$(curl -sS --max-time 10 "http://127.0.0.1:${port}/health" 2>/dev/null || true)"
  if [ -z "$body" ]; then
    echo "  $unit: /health не отвечает" >&2
    return 1
  fi
  # Без jq: он не входит в базовую установку, а тащить зависимость ради одного
  # поля в деплой-скрипт — плохая сделка.
  got="$(printf '%s' "$body" | grep -o '"revision":"[^"]*"' | head -n1 | cut -d'"' -f4)"
  if [ "$got" != "$want" ]; then
    echo "  $unit: работает $got, ожидался $want — процесс держит старый код" >&2
    return 1
  fi
  echo "  $unit: $got ✓"
}

_report_running() {
  for pair in "turbot 8000" "vk-turbot 5100"; do
    set -- $pair
    local body
    body="$(curl -sS --max-time 5 "http://127.0.0.1:$2/health" 2>/dev/null || true)"
    if [ -n "$body" ]; then
      echo "  запущено $1: $(printf '%s' "$body" | grep -o '"revision":"[^"]*"' | cut -d'"' -f4)"
    fi
  done
}

main "$@"
