#!/usr/bin/env bash
# Enable SSL/TLS on a running Db2 Community Edition container with a
# throwaway self-signed certificate, then copy the server certificate out so
# clients can trust it.
#
# Usage: db2-enable-ssl.sh <container-name> <output-cert-path>
#
# The container must have finished setup ("Setup has completed" in its logs).
# SSL listens on port 50001 alongside plain TCP on 50000.
set -euo pipefail

CONTAINER="${1:?container name required}"
CERT_OUT="${2:?output certificate path required}"
SSL_PORT=50001

# gsk9certutil in the Db2 12.1 image needs ICU, which the image does not ship.
docker exec "$CONTAINER" bash -c 'ls /usr/lib64/libicuio.so.* >/dev/null 2>&1 || dnf -y -q install libicu'

docker exec -i "$CONTAINER" su - db2inst1 -s /bin/bash <<EOF
set -euo pipefail
KEYDIR=\$HOME/ssl
KEYPW=\$(openssl rand -hex 16)
mkdir -p "\$KEYDIR"
cd "\$KEYDIR"

gsk9certutil_64 -keydb -create -db server.kdb -pw "\$KEYPW" -stash
gsk9certutil_64 -cert -create -db server.kdb -stashed -label locopy_ci \
  -dn "CN=localhost" -san_dnsname localhost -size 2048 -sigalg SHA256_WITH_RSA
gsk9certutil_64 -cert -extract -db server.kdb -stashed -label locopy_ci \
  -target server.arm -format ascii

db2 update dbm cfg using \
  SSL_SVR_KEYDB "\$KEYDIR/server.kdb" \
  SSL_SVR_STASH "\$KEYDIR/server.sth" \
  SSL_SVR_LABEL locopy_ci \
  SSL_SVCENAME $SSL_PORT >/dev/null
db2set DB2COMM=SSL,TCPIP
db2stop force >/dev/null
db2start
# Keep the database active; otherwise every connect re-activates it (~1s each).
db2 activate database testdb
EOF

docker cp "$CONTAINER":/database/config/db2inst1/ssl/server.arm "$CERT_OUT" 2>/dev/null \
  || docker exec "$CONTAINER" su - db2inst1 -c 'cat ~/ssl/server.arm' > "$CERT_OUT"
echo "SSL enabled on port $SSL_PORT; server certificate written to $CERT_OUT"
