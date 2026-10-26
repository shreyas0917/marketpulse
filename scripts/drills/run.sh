#!/usr/bin/env bash
# Entry point for `make drill N=<n>`.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
n="${1:?usage: run.sh <n>  (1-6, 8)}"
script=$(find "$DIR" -maxdepth 1 -name "${n}-*.sh" | head -1)
[[ -n "$script" ]] || { echo "no drill $n in $DIR" >&2; exit 2; }
exec bash "$script"
