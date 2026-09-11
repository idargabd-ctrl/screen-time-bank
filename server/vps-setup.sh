#!/usr/bin/env bash
#
# Настройка VPS-«двери»: HTTPS снаружи, обратный туннель внутрь.
#
# ОТЛОЖЕНО: пока сайт доступен только из домашней сети (docs/domain-setup.md).
# Понадобится, когда сайт нужен будет вне дома.
#
# VPS ничего не знает о заданиях, базе и Google. Он делает две вещи:
#   1. Caddy принимает https://uroki.<домен> и передаёт запросы на локальный
#      порт 8000 — тот, который домашний сервер пробрасывает сюда по SSH.
#   2. Пользователь tunnel принимает это SSH-соединение и не умеет ничего,
#      кроме проброса одного порта: ни командной строки, ни файлов.
#
# Почему не Cloudflare Tunnel: с 2025 года трафик к Cloudflare в России
# замедляют, сайт открывался бы через раз. VPS в российском дата-центре
# этой лотереи не имеет.
#
# Запуск НА VPS от root (Debian 12 или Ubuntu 24.04):
#     bash vps-setup.sh uroki.example.ru "ssh-ed25519 AAAA... uroki-tunnel"
#
# Второй аргумент — публичный ключ, который напечатал setup-tunnel.sh на
# домашнем сервере. Скрипт можно запускать повторно: он идемпотентен.

set -euo pipefail

say()  { printf '%s\n' "$*"; }
fail() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Запустите от root:  bash $0 ДОМЕН \"КЛЮЧ\""
[ $# -eq 2 ] || fail "Нужно два аргумента: домен сайта и публичный ключ туннеля в кавычках"

DOMAIN=$1
PUBKEY=$2
PORT=8000
TUNNEL_USER=tunnel

case "$PUBKEY" in
  ssh-ed25519\ *|ssh-rsa\ *) ;;
  *) fail "Второй аргумент не похож на публичный ключ: должен начинаться с ssh-ed25519" ;;
esac

say "1. Пакеты: Caddy и файрвол"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q caddy ufw openssh-server

say "2. Пользователь туннеля: только проброс порта $PORT"
if ! id "$TUNNEL_USER" >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin "$TUNNEL_USER"
fi
SSH_DIR="/home/$TUNNEL_USER/.ssh"
install -d -m 700 -o "$TUNNEL_USER" -g "$TUNNEL_USER" "$SSH_DIR"
# restrict снимает всё разом, port-forwarding возвращает одно, permitlisten
# сужает его до одного адреса. Шелл, файлы, агент, X11 — недоступны.
printf 'restrict,port-forwarding,permitlisten="127.0.0.1:%s" %s\n' "$PORT" "$PUBKEY" \
    > "$SSH_DIR/authorized_keys"
chown "$TUNNEL_USER:$TUNNEL_USER" "$SSH_DIR/authorized_keys"
chmod 600 "$SSH_DIR/authorized_keys"

SSHD_DROPIN=/etc/ssh/sshd_config.d/uroki-tunnel.conf
install -d -m 755 /etc/ssh/sshd_config.d
cat > "$SSHD_DROPIN" <<CONF
# Учебный сайт: пользователь обратного туннеля.
# ClientAlive — чтобы оборванное соединение с дома отпускало порт $PORT
# за полторы минуты, иначе новое подключение не сможет его занять.
Match User $TUNNEL_USER
    AllowTcpForwarding remote
    GatewayPorts no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
    ClientAliveInterval 30
    ClientAliveCountMax 3
CONF
if grep -q '^Include /etc/ssh/sshd_config.d/' /etc/ssh/sshd_config; then :; else
    sed -i '1i Include /etc/ssh/sshd_config.d/*.conf' /etc/ssh/sshd_config
fi
# Если у root уже есть ключ, пароль ему больше не нужен: подбор паролей
# на порту 22 — первое, что начинается у любого VPS в первые часы.
if [ -s /root/.ssh/authorized_keys ]; then
    printf 'PermitRootLogin prohibit-password\n' > /etc/ssh/sshd_config.d/uroki-root.conf
    say "   У root есть ключ: вход по паролю для root выключен"
else
    say "   У root нет ключа: вход по паролю оставлен (иначе вы бы потеряли доступ)"
fi
sshd -t || fail "Конфигурация sshd не прошла проверку, ничего не перезапускаю"
systemctl reload ssh 2>/dev/null || systemctl reload sshd

say "3. Caddy: https://$DOMAIN -> 127.0.0.1:$PORT"
cat > /etc/caddy/Caddyfile <<CADDY
# Учебный сайт. Сертификат Let's Encrypt Caddy получает и продлевает сам.
$DOMAIN {
    encode gzip
    reverse_proxy 127.0.0.1:$PORT

    # Туннель с дома оборван или сервер выключен. Сказать об этом честно
    # важнее, чем красиво: ребёнок должен понимать, что дело не в нём.
    handle_errors {
        @down {
            expression {http.error.status_code} == 502
        }
        header @down Content-Type "text/plain; charset=utf-8"
        respond @down "Домашний сервер сейчас не отвечает. Это не твоя ошибка — попробуй через несколько минут." 502
    }
}
CADDY
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
systemctl enable --now caddy
systemctl reload caddy

say "4. Файрвол: только 22, 80 и 443"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

say ""
say "Готово. Проверка, когда туннель с дома поднимется:"
say "    ss -ltnp | grep :$PORT      — должен слушать sshd на 127.0.0.1:$PORT"
say "    curl -sI https://$DOMAIN    — должен ответить HTTP/2 200"
