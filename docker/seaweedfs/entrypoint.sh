#!/bin/sh
# Writes the S3 identity file from env (never committed), then starts SeaweedFS
# with master + volume + filer + S3 gateway in one process (fine for local/dev).
set -eu
: "${S3_ACCESS_KEY:?}" "${S3_SECRET_KEY:?}"
mkdir -p /etc/seaweedfs
umask 077
cat > /etc/seaweedfs/s3.json <<JSON
{
  "identities": [
    {
      "name": "provcheck",
      "credentials": [{"accessKey": "${S3_ACCESS_KEY}", "secretKey": "${S3_SECRET_KEY}"}],
      "actions": ["Admin", "Read", "Write", "List", "Tagging"]
    }
  ]
}
JSON
chown seaweed:seaweed /etc/seaweedfs/s3.json 2>/dev/null || true
exec /entrypoint.sh server -dir=/data -ip.bind=0.0.0.0 -volume.max=0 \
  -master.volumeSizeLimitMB=1024 -s3 -s3.port=8333 -s3.config=/etc/seaweedfs/s3.json
