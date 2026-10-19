#!/bin/sh
cd /data/solar-fortress/src || exit 1
exec /usr/bin/python3 -u solar_status.py
