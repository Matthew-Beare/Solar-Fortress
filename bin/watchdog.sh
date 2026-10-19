#!/bin/sh

BASE="/data/solar-fortress"
SRC="$BASE/src"
LOG="$BASE/logs/solar-fortress.log"
PIDFILE="$BASE/state/watchdog.pid"

mkdir -p "$BASE/logs" "$BASE/state"

if [ -f "$PIDFILE" ]; then
    OLD_PID="$(cat "$PIDFILE" 2>/dev/null)"

    if [ -n "$OLD_PID" ] && \
       [ -r "/proc/$OLD_PID/cmdline" ] && \
       tr '\0' ' ' < "/proc/$OLD_PID/cmdline" \
       | grep -q "solar-fortress/bin/watchdog.sh"
    then
        exit 0
    fi
fi

echo $$ > "$PIDFILE"

cleanup() {
    rm -f "$PIDFILE"
}

trap cleanup EXIT INT TERM

while true
do
    if [ -f "$LOG" ] && \
       [ "$(wc -c < "$LOG")" -gt 5242880 ]
    then
        mv "$LOG" "$LOG.1"
        : > "$LOG"
    fi

    echo >> "$LOG"
    echo "========================================" >> "$LOG"
    echo "Solar Fortress watchdog start: $(date)" >> "$LOG"
    echo "========================================" >> "$LOG"

    cd "$SRC" || exit 1

    /usr/bin/python3 -u full_sim_test.py \
        >> "$LOG" 2>&1

    TEST_RESULT=$?

    if [ "$TEST_RESULT" -ne 0 ]; then
        echo "SELF TEST FAILED - CONTROLLER NOT STARTED" \
            >> "$LOG"
        sleep 30
        continue
    fi

    /usr/bin/python3 -u solar_fortress.py \
        --apply \
        --interval 1 \
        --status-every 300 \
        >> "$LOG" 2>&1

    RESULT=$?

    echo "Solar Fortress exited with code $RESULT" \
        >> "$LOG"

    echo "Restarting in 5 seconds..." \
        >> "$LOG"

    sleep 5
done
