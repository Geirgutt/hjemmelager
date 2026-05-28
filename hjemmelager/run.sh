#!/usr/bin/with-contenv bashio

export HJEMMELAGER_DATA_DIR="/data"
export HJEMMELAGER_PORT="8099"

python3 /app/server.py
