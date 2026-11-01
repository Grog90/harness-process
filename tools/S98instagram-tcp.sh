#!/bin/sh
# S98instagram-tcp — Instagram и его CDN ходят по TCP, а не QUIC.
#
# Зачем: QUIC внутри UDP-туннеля Hysteria даёт хвост p95 2–4 с на странице
# Instagram, по TCP на той же странице 0.9–1.1 с (замер 30.10.2026), а захват
# показал 9 пауз > 1 с за 30 с именно на QUIC-потоке CDN. Блокируем UDP/443 к
# подсетям Instagram — приложение само переходит на TCP и качает нормально.
#
# Обратимо одной командой:
#   /opt/etc/init.d/S98instagram-tcp stop
#
# Подсети намеренно не включают 31.13.0.0/16 и 129.134.0.0/16: там живёт
# WhatsApp, а Instagram их почти не использует. Если захват покажет QUIC-поток
# к этим подсетям — добавить, но не раньше.

PATH=/opt/sbin:/opt/bin:/usr/sbin:/usr/bin:/sbin:/bin
CHAIN=IG_TCP
PREFIXES="157.240.0.0/16 57.144.0.0/14 179.60.192.0/22 185.60.216.0/22 163.70.128.0/17 163.77.128.0/17 102.132.96.0/20"

start() {
    iptables -t mangle -N $CHAIN 2>/dev/null
    iptables -t mangle -F $CHAIN 2>/dev/null
    for p in $PREFIXES; do
        iptables -t mangle -A $CHAIN -p udp --dport 443 -d "$p" -j DROP
    done
    iptables -t mangle -C PREROUTING -j $CHAIN 2>/dev/null || \
        iptables -t mangle -I PREROUTING 1 -j $CHAIN
}

stop() {
    iptables -t mangle -D PREROUTING -j $CHAIN 2>/dev/null
    iptables -t mangle -F $CHAIN 2>/dev/null
    iptables -t mangle -X $CHAIN 2>/dev/null
}

case "$1" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; start ;;
    status)  iptables -t mangle -S $CHAIN 2>/dev/null ;;
    *) echo "usage: $0 {start|stop|restart|status}"; exit 1 ;;
esac
