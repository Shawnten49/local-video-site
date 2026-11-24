#!/bin/sh
set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_DIR"

PYTHON_BIN="${PYTHON_BIN:-${PROJECT_DIR}/.venv/bin/python}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/comic3-pycache}"

if [ ! -x "$PYTHON_BIN" ]; then
  printf 'Python executable not found: %s\n' "$PYTHON_BIN" >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  printf 'node is required for JavaScript syntax checks\n' >&2
  exit 1
fi

printf '==> Shell syntax\n'
find . -maxdepth 2 -type f -name '*.sh' \
  -not -path './.venv/*' \
  -print0 \
  | xargs -0 -n1 sh -n

printf '==> Secret scan\n'
./scripts/check_secrets.sh

printf '==> Python compile\n'
find local_video_site tests \
  -type f -name '*.py' -print0 \
  | xargs -0 "$PYTHON_BIN" -m py_compile

printf '==> JavaScript syntax\n'
find local_video_site/static -type f -name '*.js' \
  -not -path 'local_video_site/static/vendor/*' \
  -print0 \
  | xargs -0 -n1 node --check

if [ "${CHECK_STRICT:-1}" = "1" ]; then
  printf '==> Ruff\n'
  "$PYTHON_BIN" -m ruff check .

  printf '==> Mypy\n'
  "$PYTHON_BIN" -m mypy

  printf '==> Python tests with coverage\n'
  "$PYTHON_BIN" -m coverage erase
  "$PYTHON_BIN" -m coverage run -m unittest discover -s tests
  "$PYTHON_BIN" -m coverage report
else
  printf '==> Python tests\n'
  "$PYTHON_BIN" -m unittest discover -s tests
fi

if [ "${CHECK_ANDROID:-0}" = "1" ]; then
  printf '==> Android debug builds\n'
  (
    cd android-tv-native
    ./gradlew :app:assembleDebug
  )
  (
    cd android-tv-shell
    ./gradlew :app:assembleDebug
  )
fi

printf 'All checks passed.\n'
