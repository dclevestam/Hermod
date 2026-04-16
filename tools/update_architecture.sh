#!/usr/bin/env sh
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 "$DIR/generate_architecture.py" "$@"
python3 "$DIR/generate_project_context.py"
