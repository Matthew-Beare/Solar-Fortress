#!/bin/sh

if ps | grep -q \
    '[s]olar_fortress.py --apply'
then
    /data/solar-fortress/bin/status.sh
else
    echo
    echo "****************************************"
    echo "WARNING: SOLAR FORTRESS IS NOT RUNNING"
    echo "****************************************"
    echo
fi
