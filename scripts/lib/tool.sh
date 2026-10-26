#!/usr/bin/env bash
# Run a CLI tool from PATH if installed, otherwise from its pinned container image.
# Usage: scripts/lib/tool.sh <shellcheck|promtool|amtool> [args...]
# The repository root is mounted at /work and used as the working directory.
set -euo pipefail

SHELLCHECK_IMAGE="koalaman/shellcheck:v0.11.0@sha256:61862eba1fcf09a484ebcc6feea46f1782532571a34ed51fedf90dd25f925a8d"
PROMETHEUS_IMAGE="prom/prometheus:v3.15.1@sha256:fa3302279710be2f21a8301ec17a8b4f2301fd829bd113839b3b6b02b6f191a2"
ALERTMANAGER_IMAGE="prom/alertmanager:v0.34.1@sha256:e9733bafb1bdef9b00e25a21f8f99dc26a22224bf16641ad754d1649f4c3357a"

tool="$1"
shift

if command -v "$tool" >/dev/null 2>&1; then
  exec "$tool" "$@"
fi

case "$tool" in
  shellcheck) image="$SHELLCHECK_IMAGE"; entry="shellcheck" ;;
  promtool) image="$PROMETHEUS_IMAGE"; entry="promtool" ;;
  amtool) image="$ALERTMANAGER_IMAGE"; entry="amtool" ;;
  *) echo "unknown tool: $tool" >&2; exit 2 ;;
esac

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if command -v cygpath >/dev/null 2>&1; then
  root="$(cygpath -w "$root")"
fi
MSYS_NO_PATHCONV=1 exec docker run --rm -v "${root}:/work" -w /work --entrypoint "$entry" "$image" "$@"
