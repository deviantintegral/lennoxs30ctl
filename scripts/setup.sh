#!/bin/bash
set -e

cd "$(dirname "$0")/.."

uv sync --dev --all-extras
uv run pre-commit install --install-hooks || true
