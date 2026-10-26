#!/usr/bin/env bash
# Delete the local kind cluster created by scripts/kind-up.sh.
set -euo pipefail
kind delete cluster --name marketpulse
