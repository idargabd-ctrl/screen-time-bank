#!/usr/bin/env bash
#
# Настройка Wi-Fi на сервере (Debian, без графики).
#
# Зачем отдельный скрипт: у ноутбука-сервера мёртвый Ethernet и нет рабочего
# стола, поэтому единственная связь с миром — Wi-Fi, а настраивать его придётся
# вслепую с физической консоли. Набирать десяток команд руками — гарантированная
# опечатка, поэтому всё собрано в один запуск.
#
# Прописывает ДВЕ сети: домашнюю и хотспот с телефона. Подключится к той, что
# доступна. Хотспот — страховка: если в домашнем пароле опечатка, вы это
# обнаружите уже дома у ноутбука без сети, и починить будет нечем.
#
# Запуск:
#     sudo bash setup-wifi.sh
#
# Пароли нигде не отображаются и в открытом виде на диск не пишутся.

set -euo pipefail

CONF_DIR=/etc/wpa_supplicant
NET_DIR=/etc/systemd/network
COUNTRY=RU

say()  { printf '%s\n' "$*"; }
fail() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }

# --- проверки -------------------------------------------------------------

[ "$(id -u)" -eq 0 ] || fail "Запустите через sudo:  sudo bash setup-wifi.sh"

command -v wpa_passphrase >/dev/null 2>&1 || fail "Не найден wpa_passphrase. Установите пакет wpasupplicant."

# Ищем беспроводной интерфейс сами: имя вида wlp2s0 зависит от железа.
IFACE=""
for n in /sys/class/net/*; do
    [ -d "$n/wireless" ] && { IFACE=$(basename "$n"); break; }
done
[ -n "$IFACE" ] || fail "Беспроводной интерфейс не найден. Проверьте, что карта видна:  ip -br a"

say "Беспроводной интерфейс: $IFACE"
say ""

# --- опрос ----------------------------------------------------------------

ask_network() {
    # $1 — человекочитаемое название, $2 — обязательна ли сеть
    local label="$1" required="$2" ssid="" pass=""

    while :; do
        printf 'Название сети (SSID) — %s: ' "$label"
        read -r ssid
        if [ -z "$ssid" ]; then
            [ "$required" = "no" ] && return 1
            say "Пустое имя не подойдёт, введите ещё раз."
            continue
        fi
        break
    done

    while :; do
        printf 'Пароль от «%s» (не отображается): ' "$ssid"
        read -rs pass; echo
        if [ "${#pass}" -lt 8 ]; then
            say "Пароль WPA2 не короче 8 символов. Попробуйте снова."
            continue
        fi
        break
    done

    ASK_SSID="$ssid"
    ASK_PASS="$pass"
    return 0
}

block() {
    # Собирает network={...} с приоритетом.
    # grep убирает строку #psk="пароль_открытым_текстом", которую
    # wpa_passphrase добавляет комментарием.
    wpa_passphrase "$1" "$2" \
        | grep -v '^[[:space:]]*#psk=' \
        | sed "s/^}/\tpriority=$3\n}/"
}

say "Сначала домашняя сеть — основная, к ней ноутбук будет подключаться всегда."
ask_network "домашняя" "yes"
HOME_SSID="$ASK_SSID"; HOME_PASS="$ASK_PASS"

say ""
say "Теперь хотспот с телефона — запасная сеть на случай проблем дома."
say "Можно пропустить: нажмите Enter на вопросе про название."
HOTSPOT_OK=no
if ask_network "хотспот, можно пропустить" "no"; then
    HOTSPOT_SSID="$ASK_SSID"; HOTSPOT_PASS="$ASK_PASS"
    HOTSPOT_OK=yes
fi

# --- конфиг wpa_supplicant ------------------------------------------------

mkdir -p "$CONF_DIR"
CONF="$CONF_DIR/wpa_supplicant-$IFACE.conf"

if [ -f "$CONF" ]; then
    BACKUP="$CONF.bak.$(date +%Y%m%d-%H%M%S)"
    cp "$CONF" "$BACKUP"
    say ""
    say "Прежний конфиг сохранён: $BACKUP"
fi

{
    echo "ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev"
    echo "update_config=1"
    echo "country=$COUNTRY"
    echo ""
    block "$HOME_SSID" "$HOME_PASS" 10
    [ "$HOTSPOT_OK" = yes ] && block "$HOTSPOT_SSID" "$HOTSPOT_PASS" 5
} > "$CONF"

chmod 600 "$CONF"

# --- конфиг сети (адрес по DHCP) ------------------------------------------

# DHCP-клиента в системе нет ни одного, но у systemd-networkd он встроенный.
mkdir -p "$NET_DIR"
cat > "$NET_DIR/25-wireless.network" <<EOF
[Match]
Name=$IFACE

[Network]
DHCP=yes
IgnoreCarrierLoss=3s

[DHCPv4]
UseDNS=yes
RouteMetric=20
EOF

# --- DNS ------------------------------------------------------------------

if systemctl list-unit-files systemd-resolved.service >/dev/null 2>&1 \
   && systemctl list-unit-files systemd-resolved.service | grep -q systemd-resolved; then
    systemctl enable --now systemd-resolved >/dev/null 2>&1 || true
    if [ ! -L /etc/resolv.conf ]; then
        [ -f /etc/resolv.conf ] && cp /etc/resolv.conf /etc/resolv.conf.bak
        ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
    fi
    say "DNS: systemd-resolved"
else
    # Запасной путь: если resolved нет, networkd сам resolv.conf не пишет.
    printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n' > /etc/resolv.conf
    say "DNS: systemd-resolved отсутствует, прописаны публичные серверы"
fi

# --- запуск ---------------------------------------------------------------

say ""
say "Включаю службы..."
systemctl enable "wpa_supplicant@$IFACE.service" >/dev/null 2>&1
systemctl enable systemd-networkd.service >/dev/null 2>&1
ip link set "$IFACE" up 2>/dev/null || true
systemctl restart "wpa_supplicant@$IFACE.service"
systemctl restart systemd-networkd.service

say "Жду адрес от роутера (до 45 секунд)..."
ADDR=""
for _ in $(seq 1 45); do
    ADDR=$(ip -4 -br addr show "$IFACE" | awk '{print $3}')
    [ -n "$ADDR" ] && break
    sleep 1
done

say ""
say "======================================================================"
if [ -z "$ADDR" ]; then
    say "Адрес не получен."
    say ""
    say "Что смотреть:"
    say "  journalctl -u wpa_supplicant@$IFACE -n 30 --no-pager"
    say ""
    say "Частые причины:"
    say "  - опечатка в пароле: запустите этот скрипт ещё раз"
    say "  - роутер раздаёт только 5 ГГц: эта карта видит лишь 2,4 ГГц"
    say "  - сети нет рядом: проверьте список видимых сетей"
    say "      sudo /usr/sbin/iw dev $IFACE scan | grep SSID:"
    exit 1
fi

say "Подключение есть."
say "  интерфейс: $IFACE"
say "  адрес:     $ADDR"
CURRENT=$(/usr/sbin/iw dev "$IFACE" link 2>/dev/null | awk '/SSID/{print $2}')
[ -n "$CURRENT" ] && say "  сеть:      $CURRENT"
say ""

if ping -c 2 -W 3 1.1.1.1 >/dev/null 2>&1; then
    say "Интернет доступен."
else
    say "Адрес есть, но интернет не отвечает — проверьте роутер."
fi

say ""
say "Настройки сохранены и переживут перезагрузку."
say ""
say "Дальше — подключение с рабочего ноутбука по SSH. На этой машине:"
say "    sudo apt install -y openssh-server"
say ""
say "Затем на рабочем ноутбуке (Windows), подставив адрес без /24:"
say "    ssh $(logname 2>/dev/null || echo aidar)@${ADDR%%/*}"
say "======================================================================"
