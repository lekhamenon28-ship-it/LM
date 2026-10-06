#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if ! command -v node >/dev/null 2>&1 && [ -x .tools/bin/node ]; then
  export PATH="$PWD/.tools/bin:$PATH"
fi
exec npm run dev
