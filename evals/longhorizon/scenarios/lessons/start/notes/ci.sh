#!/bin/sh
exec python -m pytest -q -p no:cacheprovider "$@"
