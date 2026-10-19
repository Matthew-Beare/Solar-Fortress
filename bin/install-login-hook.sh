#!/bin/sh

ROOT_HOME="$(
    awk -F: \
        '$1 == "root" { print $6 }' \
        /etc/passwd
)"

if [ -z "$ROOT_HOME" ]; then
    ROOT_HOME="/home/root"
fi

PROFILE="$ROOT_HOME/.profile"

touch "$PROFILE"

if ! grep -q \
    'SOLAR_FORTRESS_AUTO_DASHBOARD' \
    "$PROFILE" 2>/dev/null
then
    cat >> "$PROFILE" <<'EOF'

# SOLAR_FORTRESS_AUTO_DASHBOARD
if [ -t 0 ] && [ -t 1 ] && \
   [ -x /data/solar-fortress/bin/login-dashboard.sh ]
then
    /data/solar-fortress/bin/login-dashboard.sh
fi
EOF
fi
