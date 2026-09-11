#!/usr/bin/env bash
#
# Установка Docker на сервер (Debian trixie).
#
# Ставим из официального репозитория Docker, а не из Debian. Причина
# конкретная: в trixie пакета с compose v2 нет вообще — есть только старый
# docker-compose на Python, который не понимает синтаксис нашего
# docker-compose.yml (там ключ name: верхнего уровня). Официальный репозиторий
# даёт docker-compose-plugin, то есть команду «docker compose».
#
# Проверено перед написанием: https://download.docker.com/linux/debian
# содержит выпуск trixie, архитектуру amd64 и компонент stable.
#
# Запуск:
#     sudo bash ~/child-mind/server/install-docker.sh
#
# Скачивает около 150 МБ. Образы Home Assistant и familylink-auth — отдельно,
# это ещё примерно 2 ГБ.

set -euo pipefail

KEYRING=/etc/apt/keyrings/docker.asc
LIST=/etc/apt/sources.list.d/docker.list

say()  { printf '%s\n' "$*"; }
fail() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Запустите через sudo:  sudo bash $0"

ARCH=$(dpkg --print-architecture)
[ "$ARCH" = amd64 ] || fail "Ожидалась архитектура amd64, обнаружена $ARCH"

. /etc/os-release
[ "${VERSION_CODENAME:-}" = trixie ] || say "Внимание: выпуск ${VERSION_CODENAME:-неизвестен}, скрипт писался под trixie."

# Кому вернуть права на docker без sudo.
TARGET_USER="${SUDO_USER:-}"
[ -n "$TARGET_USER" ] || fail "Не удалось определить пользователя. Запускайте через sudo, а не от root напрямую."

if command -v docker >/dev/null 2>&1; then
    say "Docker уже установлен: $(docker --version)"
    say "Переустановку не делаю. Если нужно обновить — apt-get upgrade."
    NEED_INSTALL=no
else
    NEED_INSTALL=yes
fi

if [ "$NEED_INSTALL" = yes ]; then
    say "Готовлю репозиторий Docker..."
    apt-get update -qq
    apt-get install -y -qq ca-certificates wget >/dev/null

    install -m 0755 -d /etc/apt/keyrings

    # wget, а не curl: curl в этой системе не установлен.
    wget -qO "$KEYRING" https://download.docker.com/linux/debian/gpg \
        || fail "Не удалось скачать ключ репозитория. Проверьте сеть."
    chmod a+r "$KEYRING"

    printf 'deb [arch=%s signed-by=%s] https://download.docker.com/linux/debian %s stable\n' \
        "$ARCH" "$KEYRING" "$VERSION_CODENAME" > "$LIST"

    say "Устанавливаю (около 150 МБ)..."
    apt-get update -qq
    apt-get install -y docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
fi

# Автозапуск при загрузке. На Debian это systemd, вход пользователя не нужен —
# после отключения света машина поднимет контейнеры сама.
systemctl enable --now docker >/dev/null 2>&1 || true

# Без этого каждая команда docker требовала бы sudo.
if id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx docker; then
    GROUP_ADDED=no
else
    usermod -aG docker "$TARGET_USER"
    GROUP_ADDED=yes
fi

say ""
say "======================================================================"
say "Docker:  $(docker --version)"
say "Compose: $(docker compose version 2>/dev/null || echo 'НЕ НАЙДЕН — это проблема')"
say "Служба:  $(systemctl is-active docker), автозапуск $(systemctl is-enabled docker)"
say ""

if [ "$GROUP_ADDED" = yes ]; then
    say "Пользователь $TARGET_USER добавлен в группу docker."
    say ""
    say "ВАЖНО: чтобы это подействовало, нужно переподключиться."
    say "  1) наберите  exit"
    say "  2) зайдите заново:  ssh agent"
    say "  3) проверьте:  docker ps"
    say ""
    say "Если docker ps ругается на права — значит сессия старая, перезайдите."
else
    say "Пользователь $TARGET_USER уже в группе docker."
fi

say ""
say "Дальше — образы Home Assistant и familylink-auth, около 2 ГБ:"
say "    cd ~/child-mind && docker compose pull"
say "======================================================================"
