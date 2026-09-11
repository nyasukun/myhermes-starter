#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
PYTHONPATH=src "${MYHERMES_PYTHON:-python3}" -m unittest discover -s tests -p 'test_*.py' -v
