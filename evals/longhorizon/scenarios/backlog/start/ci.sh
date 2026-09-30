#!/bin/sh
# CI runs the tests in India Standard Time, like the shop's server.
TZ=Asia/Kolkata exec python -m pytest -q "$@"
