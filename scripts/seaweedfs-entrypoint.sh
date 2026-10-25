#!/bin/sh
set -eu

if [ -z "${S3_ACCESS_KEY:-}" ] || [ -z "${S3_SECRET_KEY:-}" ]; then
  echo "S3_ACCESS_KEY and S3_SECRET_KEY must be configured." >&2
  exit 1
fi

case "${S3_ACCESS_KEY}${S3_SECRET_KEY}" in
  *[!A-Za-z0-9_-]*)
    echo "S3 credentials may contain only letters, digits, underscores, and hyphens." >&2
    exit 1
    ;;
esac

umask 077
s3_config=/tmp/seaweedfs-s3.json
printf '{"identities":[{"name":"incubrix","credentials":[{"accessKey":"%s","secretKey":"%s"}],"actions":["Admin","Read","List","Tagging","Write"]}]}\n' \
  "$S3_ACCESS_KEY" "$S3_SECRET_KEY" > "$s3_config"
chown seaweed:seaweed "$s3_config"
chmod 600 "$s3_config"

exec /entrypoint.sh mini \
  -dir=/data \
  -s3.config="$s3_config" \
  -bucket=incubrix-audio
