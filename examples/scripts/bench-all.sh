#!/usr/bin/env bash
# Run the full benchmark suite and write the snapshot to disk.
#
# Usage:
#   examples/scripts/bench-all.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="$REPO_ROOT/benchmarks/results"
mkdir -p "$OUT_DIR"

python -m benchmarks \
  --output "$OUT_DIR/snapshot.json" \
  --markdown "$OUT_DIR/snapshot.md"

echo
echo "snapshot written to:"
echo "  $OUT_DIR/snapshot.json"
echo "  $OUT_DIR/snapshot.md"
