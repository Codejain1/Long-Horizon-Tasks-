#!/bin/sh
# CI runs in India Standard Time, like the production servers.
TZ=Asia/Kolkata exec python -m pytest -q "$@"
