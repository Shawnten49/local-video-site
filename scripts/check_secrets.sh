#!/bin/sh
set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_DIR"

status=0

forbidden_files="$(
  git ls-files \
    | grep -E '(^|/)(local\.properties|\.env|\.delete-password|\.lan-token)$|\.(db|sqlite|sqlite3|mp4|mkv|avi|rmvb|mov|webm|m3u8|jpg|jpeg|png|webp|log|jks|keystore|pem|key)$' \
    || true
)"
if [ -n "$forbidden_files" ]; then
  printf '%s\n' "Forbidden sensitive or runtime files are tracked:" >&2
  printf '%s\n' "$forbidden_files" >&2
  status=1
fi

secret_files="$(
  git grep -Il -E \
    '(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----)' \
    -- . \
    ':!local_video_site/static/vendor/**' \
    || true
)"
if [ -n "$secret_files" ]; then
  printf '%s\n' "Potential credentials found in:" >&2
  printf '%s\n' "$secret_files" >&2
  status=1
fi

personal_files="$(
  git grep -Il -E '(/Users/shawn|192\.168\.0\.107)' \
    -- . \
    ':!scripts/check_secrets.sh' \
    || true
)"
if [ -n "$personal_files" ]; then
  printf '%s\n' "Personal paths or fixed LAN IPs found in:" >&2
  printf '%s\n' "$personal_files" >&2
  status=1
fi

if command -v gitleaks >/dev/null 2>&1; then
  printf '%s\n' "==> Gitleaks"
  gitleaks detect --source . --no-git --redact
fi

if [ "$status" -ne 0 ]; then
  exit 1
fi

printf '%s\n' "Secret scan passed."
