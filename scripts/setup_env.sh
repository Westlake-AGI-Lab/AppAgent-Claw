#!/usr/bin/env bash

set -euo pipefail

# ROOT_DIR is the current skill bundle root, not the caller's shell cwd.
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT_DIR"

echo "Skill root: $ROOT_DIR"

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r assets/requirements.txt

echo "Environment ready: $ROOT_DIR/.venv"
