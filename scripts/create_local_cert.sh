#!/usr/bin/env bash
# Создаёт локальный CA и сертификат для HTTPS на IP-адресе в локальной сети.
# Использование:
#   scripts/create_local_cert.sh [IP]
# Без аргумента IP определяется автоматически.
set -euo pipefail

cd "$(dirname "$0")/.."
IP="${1:-$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -n1)}"
if [[ -z "$IP" ]]; then
    echo "Не удалось определить IP. Укажите его: scripts/create_local_cert.sh 10.0.0.5" >&2
    exit 1
fi

OUT="data/certs"
mkdir -p "$OUT"
cd "$OUT"

openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
    -keyout upk-ca.key -out upk-ca.crt \
    -subj "/CN=UPK Local CA/O=College DGU" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" >/dev/null 2>&1

openssl req -newkey rsa:2048 -nodes \
    -keyout upk.key -out upk.csr \
    -subj "/CN=$IP/O=College DGU" >/dev/null 2>&1

cat > upk.ext <<EOF
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:$IP,IP:127.0.0.1,DNS:localhost
EOF

openssl x509 -req -in upk.csr \
    -CA upk-ca.crt -CAkey upk-ca.key -CAcreateserial \
    -out upk.crt -days 825 -sha256 -extfile upk.ext >/dev/null 2>&1

cat upk.crt upk-ca.crt > upk-chain.crt
chmod 600 upk-ca.key upk.key
chmod 644 upk-ca.crt upk.crt upk-chain.crt upk.ext

echo "Готово. IP: $IP"
echo "Сертификат сервера: $PWD/upk-chain.crt"
echo "Ключ сервера:       $PWD/upk.key"
echo "CA для телефона:    $PWD/upk-ca.crt"
