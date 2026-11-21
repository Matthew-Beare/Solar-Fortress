# Solar Fortress adaptive charging and storm latch: staged implementation

Status: feature branch only. NOT MERGED, NOT INSTALLED, NOT SWITCHING INVERTER MODES.
The running Venus OS Pi is newer than this repository snapshot. Reconcile the
running files and back up its configuration/state before any deployment.

## Components and their permissions

- src/charge_monitor.py: optional loopback-only 127.0.0.1:8080/api/status
  observer, configured poll default 60 seconds, persistent SQLite history.
- src/charge_learning.py: trains on COMPLETED, uninterrupted grid-charge
  sessions only. Completion requires SmartShunt TimeSinceLastFullCharge
  identifying an actual full event; SOC 100 alone never counts.
- src/storm_watch.py: read-only forecast + battery readiness decision. Learned
  time to SmartShunt full (which includes observed absorption/taper) is compared
  against the conservative capacity/current fallback when available. A
  configurable 4-hour storm buffer is then added, never doubled.
- src/storm_latch.py: persistent SQLite state machine. Storm Mode can request
  pre-storm grid charging, then pass-through once the storm begins or a
  verified full charge and separately configured balancing hold complete.
  Its output is an ADVISORY; no DBus or inverter-control endpoint is used.
- src/nws_client.py: independent read-only official HTTPS National Weather
  Service forecast and winter-weather alert fetch; requires accurate site
  coordinates specified in the authenticated operator GUI.
- src/operator_cycle.py: orchestrates the above. It can poll the Pi every
  configured minute, fetch NWS at the configured 15-minute interval,
  recalculate the charge deadline from the newest SOC and trained model,
  persist latch state, and emit advisory JSON. API outages preserve saved
  state and never imply that a storm has ended.
- src/operator_portal.py: loopback 127.0.0.1:8082, password-protected.
  Saves validated editable settings with CSRF protection. It does NOT restart
  the old controller, authorize a control API, or apply unintegrated settings.

## Adaptive charging time model

A completed charging session records timestamped SOC readings until the
SmartShunt confirms a full-charge event. That includes charging taper near
full; elapsed time from each recorded SOC to full becomes training data.
The estimator selects a conservative recent observed completion duration
from independent sessions with nearby starting SOC values.

The defaults are editable via the authenticated GUI:
- Automatic learning enabled, sample status every 60 seconds
- Minimum completed full-charge sessions: 2
- Recent completed sessions retained for prediction: 12
- Maximum SOC match difference: 5 percentage points
- Conservative multiplier on the slowest matching session: 1.10
- Allowed sampling gap: 20 minutes
- Sample spacing: 60 seconds
- Minimum positive battery net charge current: 1 amp
- Near-full taper allowance: keep an existing charge session active at or
  above 95% SOC even if measured battery current falls to -2 amps (both
  thresholds independently editable); only a SmartShunt full event can end
  that session successfully
- Minimum SOC gain for a session: 5 percentage points
- Optional bank capacity in Ah, conservative DC current cap in amps, and
  separate manual finishing-time allowance while the model is untrained

The software must never call these learned values "measured" until the
physical system has accumulated actual verified sessions. Missing history
falls back to clearly labelled estimated bulk charge duration, if nominal
bank capacity and safe charger rate are known. It cannot invent missing
measurements. Learned ETA never automatically changes Victron charge rate.

## Storm timing and pass-through latch

With the normal target at 100% SOC and a 4-hour planning buffer:
- Learned/estimated 12 hours to actual full charge -> recommend charging
  when forecast onset is 16 hours away.
- If severe weather meets its separate immediate threshold (0.5 inch ice
  forecast in the next 24 hours by default), recommend charging immediately
  without waiting for any older Watch or forecast confirmation.
- On activation, persist the forecast event's predicted end of accumulation.
  Later fresh forecasts can EXTEND that end, not shorten it.
- Continue requesting pass-through after the predicted onset and through the
  entire storm. Do not voluntarily drain battery reserve while on grid.
- Keep the latch until last predicted accumulation end + 12 HOURS by default.
  Post-storm hold is independently editable 0..168 hours, and the interval
  joining successive forecast precipitation periods defaults to 12 hours.
- Default release also requires fresh NWS weather (max issue age 6 hours,
  configurable) and an independently confirmed CLEAR active winter-alert
  result. No forecast or alert response means UNKNOWN, never CLEAR.
- The operator's explicit master Storm Mode OFF overrides the advisor.
  Local AC/BMS/manual safeguards are authoritative when inverter integration
  is eventually added.
- Configurable full-charge balancing/maintenance hold after a real SmartShunt
  event is SEPARATE: default 60 minutes, adjustable 0..1440 minutes; this
  hold is NOT silently merged into the storm planning buffer. A full SOC
  display does not prove BMS cell balancing has completed.

Every ordinary threshold/duration in this design can be edited in the
authenticated portal. Hardware current ratings, battery/BMS protections,
wire limits and safe inverter modes cannot be overridden through the GUI.

## Controlled test setup (does NOT actuate hardware)

First inspect and back up the ACTUAL production Pi. Never overwrite its newer
controller code with the public GitHub controller snapshot.

On an isolated TEST copy of the Pi config and scratch state directory,
initialize the admin password with the operator portal's init-password
command. Then point the read-only operator process at test paths:

    python3 src/operator_cycle.py --once \
      --config /test/controller.json \
      --learning-db /test/charge-learning.sqlite \
      --latch-db /test/storm-latch.sqlite \
      --forecast-file /test/nws-forecast.json \
      --alerts-file /test/nws-alerts.json

For continuous automatic learning and weather checks only AFTER the
site coordinates are configured and permissions reviewed, add --loop
--fetch-nws. In that mode, the local status is sampled every configured
learning_poll_seconds (60 by default) and official NWS weather/active alerts
are fetched every configured check_interval_minutes (15 by default). The
output mode is an advisory, never an electrical instruction. The downloaded
forecast and alert snapshots are local-only files written atomically.

The NWS forecast and alert files are written atomically. A network error
retains the last saved file, but age checks prevent stale data from being
treated as fresh. SQLite histories and the active storm latch survive
process restarts and are not automatically discarded when data disappears.

## Remaining production blockers

1. Inspect the real deployed Venus OS controller, its 10-day maintenance and
   post-full balancing logic, and live JSON schema. Preserve newer fixes.
2. Confirm true 600-Ah bank rating and *attainable* AC/DC charge rate, limits,
   usable charging path and whether SmartShunt full event timing is reliable.
3. Run manual read-only training against real completed full-charge sessions.
   Observe real absorption taper, slow finishes, missing telemetry and restarts.
4. Validate official NWS grid accumulation periods and live active alerts for
   the actual Boomer HQ coordinates. No coordinates are hard-coded.
5. Test the local Pi's atomic state ownership, filesystem persistence and
   ability to boot and operate without VM102, internet or WireGuard.
6. Integrate a tightly scoped Pi-side pass-through/grid-charge request with
   authentication, manual override and local electrical safety precedence.
   Do NOT apply this repo snapshot directly over the running controller.
7. Test actual AC outage, battery discharge, failed full-charge event,
   storm-duration extension, multi-day weather, and safe post-storm exit
   under on-site operator supervision before enabling any actuation.

CI tests use Python simulation, scratch SQLite DBs, and offline NWS fixtures.
They do not establish that a real inverter will charge or change mode safely.
