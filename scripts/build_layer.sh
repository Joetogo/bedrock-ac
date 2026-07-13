#!/usr/bin/env bash
# Assemble the Lambda shared layer. _shared must be importable as a top-level
# package inside every function. SAM's python layer builder copies the
# ContentUri contents under python/ itself, so we stage _shared at the layer
# root (NOT under python/) to avoid a python/python/_shared double-nesting.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAYER="$ROOT/build/layer"

rm -rf "$LAYER"
mkdir -p "$LAYER/_shared"
cp "$ROOT/src/_shared/"*.py "$LAYER/_shared/"
touch "$LAYER/_shared/__init__.py"

echo "layer assembled at build/layer (_shared at root; SAM adds python/)"
