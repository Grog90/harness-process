#!/bin/sh
# ig-capture.sh [секунды] [ip устройства] — разовый захват трафика Instagram на LAN.
#
# Зачем: панель netmon видит только снимок текущих соединений и не видит,
# что происходило внутри потока — паузы, неотвеченные рукопожатия, RST.
# Этот скрипт снимает пакеты на br0 (до туннеля), считает по каждому потоку
# пакеты/байты в обе стороны и максимальную паузу, и печатает отчёт.
#
# Никаких запросов в Instagram не делает: только слушает уже идущий трафик.
#
# Пример: ig-capture.sh 45 192.168.1.64

PATH=/opt/sbin:/opt/bin:/usr/sbin:/usr/bin:/sbin:/bin
SECS=${1:-45}
HOST=${2:-}
IFACE=br0

NETS="net 157.240.0.0/16 or net 57.144.0.0/16 or net 57.145.0.0/16 or net 31.13.0.0/16 or net 179.60.192.0/22 or net 185.60.216.0/22 or net 102.132.96.0/20 or net 163.70.128.0/17"
FILTER="(tcp port 443 or udp port 443) and ($NETS)"
[ -n "$HOST" ] && FILTER="$FILTER and host $HOST"

RAW=/tmp/ig-cap-$$.txt
rm -f "$RAW"
tcpdump -i "$IFACE" -n -tt -S -s 96 "$FILTER" > "$RAW" 2>/dev/null &
TPID=$!
sleep "$SECS"
kill "$TPID" 2>/dev/null
wait "$TPID" 2>/dev/null

awk -v SECS="$SECS" '
function priv(a) {
  if (a ~ /^192\.168\./) return 1
  if (a ~ /^10\./) return 1
  if (a ~ /^172\.(1[6-9]|2[0-9]|3[01])\./) return 1
  return 0
}
/ IP / {
  ts=$1+0; src=$3; dst=$5; sub(":$","",dst); sub(":$","",src)
  if (src == "" || dst == "") next
  if (priv(src)) { key=src" -> "dst; dir="out" } else { key=dst" -> "src; dir="in" }
  if (!(key in first)) { first[key]=ts; prev[key]=ts; gapmax[key]=0; gaps[key]=0; pk[key]=0; bo[key]=0; bi[key]=0; po[key]=0; pi[key]=0; syn[key]=0; synack[key]=0; rst[key]=0; pr[key]="tcp" }
  if ($0 ~ /UDP,/) pr[key]="udp"
  gap = ts - prev[key]
  if (gap > gapmax[key]) gapmax[key]=gap
  if (gap > 1.0) gaps[key]++
  prev[key]=ts
  len=0
  if (match($0, /length [0-9]+/)) { len=substr($0, RSTART+7, RLENGTH-7) + 0 }
  pk[key]++
  if (dir=="out") { po[key]++; bo[key]+=len } else { pi[key]++; bi[key]+=len }
  if (dir=="out" && $0 ~ /Flags \[S\]/) syn[key]++
  if (dir=="in"  && $0 ~ /Flags \[S\.\]/) synack[key]++
  if ($0 ~ /Flags \[R/) rst[key]++
}
END {
  n=0
  for (k in first) { n++; K[n]=k }
  for (i=1;i<=n;i++) for (j=i+1;j<=n;j++) if (gapmax[K[j]] > gapmax[K[i]]) { t=K[i]; K[i]=K[j]; K[j]=t }

  tb=0; tp=0
  for (k in pk) { tp+=pk[k]; tb+=bo[k]+bi[k] }
  printf "окно: %s с, потоков: %d, пакетов: %d, байт: %d\n\n", SECS, n, tp, tb

  printf "Потоки по величине паузы (топ-12):\n"
  printf "  %-46s %-4s %7s %7s %9s %9s %7s %8s\n", "flow", "pr", "pk_out", "pk_in", "KB_out", "KB_in", "pause", "gaps>1s"
  shown=0
  for (i=1;i<=n && shown<12;i++) {
    k=K[i]
    printf "  %-46s %-4s %7d %7d %9.1f %9.1f %6.1fс %6d\n", k, pr[k], po[k], pi[k], bo[k]/1024, bi[k]/1024, gapmax[k], gaps[k]
    shown++
  }
  if (n==0) print "  нет пакетов к подсетям Instagram — приложение молчало"

  printf "\nНеотвеченные потоки (phone отправил, ответа нет):\n"
  bad=0
  for (k in first) {
    if (po[k] > 0 && pi[k] == 0) { printf "  %-46s пакетов %d, SYN %d\n", k, po[k], syn[k]; bad++ }
  }
  if (bad==0) print "  нет"

  printf "\nTCP-рукопожатия в окне:\n"
  hs=0
  for (k in first) if (syn[k] > 0) { printf "  %-46s SYN %d, SYN-ACK %d\n", k, syn[k], synack[k]; hs++ }
  if (hs == 0) print "  не видно (SYN раньше окна либо весь трафик QUIC/UDP)"

  printf "\nRST:\n"
  rs=0
  for (k in first) if (rst[k] > 0) { printf "  %-46s RST %d\n", k, rst[k]; rs++ }
  if (rs==0) print "  нет"
  exit 0
}
' "$RAW"
rm -f "$RAW"
