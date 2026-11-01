#!/opt/bin/sh
# Restore the Instagram TCP-only rules after Keenetic rebuilds IPv4 firewall tables.
[ "$type" = 'iptables' ] || exit 0
/opt/etc/init.d/S98instagram-tcp start
