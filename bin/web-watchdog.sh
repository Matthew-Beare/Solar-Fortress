#!/bin/sh

BASE="/data/solar-fortress"
LOG="$BASE/logs/web-dashboard.log"
PIDFILE="$BASE/state/web-watchdog.pid"

mkdir -p "$BASE/logs" "$BASE/state"

if [ -f "$PIDFILE" ]; then
    OLD_PID="$(cat "$PIDFILE" 2>/dev/null)"

    if [ -n "$OLD_PID" ] && \
       [ -r "/proc/$OLD_PID/cmdline" ] && \
       tr '\0' ' ' < "/proc/$OLD_PID/cmdline" \
       | grep -q "web-watchdog.sh"
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
    echo "Web dashboard start: $(date)" >> "$LOG"

    cd "$BASE/src" || exit 1

    /usr/bin/python3 -u web_dashboard.py \
        >> "$LOG" 2>&1

    RESULT=$?

    echo \
        "Web dashboard exited with code $RESULT" \
        >> "$LOG"

    sleep 5
done
