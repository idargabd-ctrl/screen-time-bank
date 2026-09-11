#!/usr/bin/env bash
#
# Обратный туннель с домашнего сервера на VPS.
#
# ОТЛОЖЕНО: пока сайт доступен только из домашней сети (docs/domain-setup.md).
# Понадобится, когда сайт нужен будет вне дома.
#
# Домашний ноутбук стоит за роутером или хотспотом, белого IP-адреса у него
# нет, и снаружи к нему не подключиться. Поэтому он подключается сам: держит
# SSH-соединение к VPS и просит его «всё, что придёт тебе на 127.0.0.1:8000,
# передавай мне на 127.0.0.1:8000». Там на 8000 слушает контейнер uroki-web.
#
# Соединение держит systemd: упало — поднимет через 10 секунд, перезагрузка —
# поднимет само. Никаких autossh и дополнительных пакетов не нужно.
#
# Запуск НА ДОМАШНЕМ СЕРВЕРЕ:
#     sudo bash ~/child-mind/server/setup-tunnel.sh IP-АДРЕС-VPS
#
# Первый запуск печатает публичный ключ — его нужно передать vps-setup.sh.
# Повторный запуск с другим IP просто перенастраивает адрес.

set -euo pipefail

say()  { printf '%s\n' "$*"; }
fail() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Запустите через sudo:  sudo bash $0 IP-АДРЕС-VPS"
[ $# -eq 1 ] || fail "Нужен один аргумент: IP-адрес VPS"

VPS=$1
PORT=8000
DIR=/etc/uroki
KEY=$DIR/tunnel_key
UNIT=/etc/systemd/system/uroki-tunnel.service

command -v ssh >/dev/null || fail "Нет команды ssh: apt-get install openssh-client"

install -d -m 700 "$DIR"
if [ ! -f "$KEY" ]; then
    say "1. Создаю ключ туннеля (без пароля: его использует служба, не человек)"
    ssh-keygen -q -t ed25519 -N "" -C "uroki-tunnel" -f "$KEY"
else
    say "1. Ключ уже есть, оставляю"
fi

say "2. Служба systemd"
cat > "$UNIT" <<UNIT
[Unit]
Description=Учебный сайт: обратный туннель к VPS
After=network-online.target
Wants=network-online.target

[Service]
# -N: без команды, только проброс. ExitOnForwardFailure: если VPS ещё держит
# порт за оборванным соединением, выйти и дать systemd повторить позже —
# иначе ssh висел бы «подключённым», но без проброса.
ExecStart=/usr/bin/ssh -N -T \
    -i $KEY \
    -o IdentitiesOnly=yes \
    -o UserKnownHostsFile=$DIR/known_hosts \
    -o StrictHostKeyChecking=accept-new \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes \
    -R 127.0.0.1:$PORT:127.0.0.1:$PORT \
    tunnel@$VPS
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable uroki-tunnel >/dev/null 2>&1
systemctl restart uroki-tunnel

say ""
say "Публичный ключ — его нужно передать vps-setup.sh на VPS (вся строка целиком):"
say ""
cat "$KEY.pub"
say ""
say "Служба запущена и будет пробовать подключаться каждые 10 секунд,"
say "пока VPS не примет ключ. Проверить:  systemctl status uroki-tunnel"
