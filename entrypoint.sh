#!/bin/sh
# Старт контейнера: том принадлежит root, приложение — uid 10001 (26.11.2026).
#
# Почему так. Named volume монтируется root:root, поэтому не-root не мог создать /data/a,
# и первая же оплата в x402-режиме падала бы на mkdir (проверено: Permission denied).
# Здесь root делает одну вещь — отдаёт каталог состояния пользователю — и сразу сбрасывает
# привилегии через setpriv (он есть в python:3.12-slim). Приложение под root не работает.
set -eu

STATE_DIR="${DRIFT_STATE_DIR:-/data/a}"
mkdir -p "$STATE_DIR"
chown -R 10001:10001 "$(dirname "$STATE_DIR")"

exec setpriv --reuid=10001 --regid=10001 --clear-groups -- "$@"
