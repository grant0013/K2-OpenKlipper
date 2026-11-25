#!/bin/sh
# Keep formatting configuration and tooling separate from runtime source.
set -eu
SRC=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
FORMATTER=${CLANG_FORMAT:-clang-format}
case "${1:-}" in
    --check) find "$SRC/src" -type f \( -name '*.c' -o -name '*.h' \) -exec "$FORMATTER" --style="file:$SRC/developer-tools/.clang-format" --dry-run --Werror {} + ;;
    "") find "$SRC/src" -type f \( -name '*.c' -o -name '*.h' \) -exec "$FORMATTER" --style="file:$SRC/developer-tools/.clang-format" -i {} + ;;
    *) echo "Usage: $0 [--check]" >&2; exit 2 ;;
esac
