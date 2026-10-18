#!/bin/sh
# CI runs on a server in New York.
TZ=America/New_York exec python -m pytest -q -p no:cacheprovider "$@"
