# Solar Fortress Operator Controls (staged, not deployed)

This branch contains a conservative settings foundation for the Venus OS Pi.
It does NOT write to the inverter, install itself as a service, or enable
automatic storm charging. The deployed Pi controller is newer than this
repository snapshot: reconcile the current live files before deployment.

## Separate privileges and network boundary

- The existing dashboard is intended to remain view-only for Commander Boomer.
- The operator portal is a separate HTTP listener at 127.0.0.1:8082 only.
- Do not port-forward 8082 from the router or bind it to 0.0.0.0.
- Access the portal from a trusted administrator through an SSH tunnel,
  such as local port 18082 forwarded to Pi loopback port 8082.
- Initial administrator password is entered locally using the init-password
  command and never hard-coded or committed. It is scrypt-hashed with a
  random salt. The user can change it on the protected settings screen.
- POST requests require a CSRF token. No settings endpoint is exposed to
  the anonymous status/dashboard viewer.
- At present, 8082 uses HTTP over loopback/SSH, not public HTTPS.

## Startup on a TEST copy of the Pi filesystem

Review the actual deployed controller files first and save a backup. Do
not replace production src/ or controller.json with the old Git version.

1. Create a test directory and copy the actual controller.json into it.
2. Run operator_portal.py init-password with --auth and --config set to the
   test directory's file paths. Enter a 14+ character password.
3. Start operator_portal.py serve with the same paths and a free port.
4. Use an SSH tunnel to access the local web page. Verify both viewer
   isolation and successful authenticated access.
5. Run test_operator_policy.py and test_operator_portal.py regressions before
   attempting any production integration.

## Saved data and behavioural boundaries

The portal preserves unrelated JSON keys while editing:
- Emergency grid charge, pass-through and battery return SOC thresholds
- Full-charge maintenance interval (default 10 days)
- Retry request interval (default 4 days)
- Storm Mode enabled/disabled, immediate storm-onset lookahead (default 12 hours)
- Early Storm Watch lead (default 48 hours, configurable 1 to 120 hours; 8 works)
- Forecast comparison delay (default 24 hours, configurable 1 to 48 hours)
- Charge-time safety margin (default 4 hours, configurable 0 to 48 hours; 8 works independently)
- Snow and ice accumulation evaluation period (default rolling 24 hours), separate from warning lead
- Forecast check interval (default 15 minutes; editable 5 to 120 minutes)
- Immediate forecast horizon (default 24 hours, independently editable)
- Severe ice override: 0.50 inches in the immediate forecast horizon (editable)
- Optional severe snow and wind overrides, each editable or disabled
- Per-condition snow (6 in), ice (0.25 in), wind (disabled by default)
- Storm target SOC 100% and storm reserve SOC 90%, each editable inside validated bounds

The policy stage evaluates the forward-looking accumulation window each time it
is called. It does NOT automatically poll NWS every 15 minutes yet. That
scheduler belongs on the Boomer HQ telemetry server and still needs to be
deployed, then linked to this configuration without granting uncontrolled
hardware access. A positive accumulation forecast that begins after the lead
horizon must not trigger merely because a 24-hour total is high.


A separate pure planning module, src/storm_watch.py, watches for a qualifying
event inside the 48-hour watch and can compare the event's type and predicted
onset against a snapshot taken at least 24 hours earlier. Confirmation
requires distinct official NWS updateTime values; merely rereading a cached
forecast is not independent confirmation.

Historical confirmation is informational, never a prerequisite for charging.
The planner reevaluates the freshest weather, latest SOC, and attainable charger
current every time it runs. It recommends charging if: (1) severe ice reaches
0.50 inches within the next 24 hours (or another enabled urgent threshold),
(2) a qualified event has persisted in different NWS forecast revisions,
or (3) time to onset is less than or equal to estimated charging duration
plus the editable four-hour margin. A 14-hour charging estimate creates an
18-hour planning deadline with a four-hour buffer and a 22-hour deadline
with an eight-hour buffer. A narrow watch length cannot block the urgent
24-hour forecast override. This remains advisory code.
NWS source accuracy, charger capabilities, available AC and SmartShunt data
must be verified before implementing a real state-changing request.

Do not end an existing storm latch just because the next forecast is clear.
The live controller must own persistent storm state, manual override, hold
and safe release logic separately. No such actuator is installed here.

The portal writes settings atomically and attempts to preserve existing file
owner and mode. It does not restart the controller. The current public
controller reads threshold settings at startup. The newer deployed maintenance
controller must be inspected to map its actual configuration schema and to
apply settings safely. Storm Mode in this branch remains non-actuating; there
is no Pi command endpoint for the server.

A forecast window is not a charging-time guarantee. A system with 600 Ah
capacity and 50 actual DC charging amperes needs a minimum 7.2 hours to
replenish 40% to 100% SOC, before absorption taper and concurrent loads.

## Safety and recovery

- Keep BMS safety settings and hardware protections outside the edit screen.
- Refuse inconsistent thresholds (charge < pass-through < return).
- Treat missing NWS data as unknown, never as zero or evidence of safety.
- Never infer a completed maintenance charge from a timeout or darkness.
- Store the true last-full-charge event from the SmartShunt independently.
- Use dry-run decisions and controlled tests before any automatic actuator.
- The Pi must continue safely without GREPmox, Internet, WireGuard or this
  portal; any server-issued request needs authenticated, narrow Pi validation.

Master storm disable is authoritative: it turns off severe overrides too.
Configuration is not applied to the real Pi until a separate validated
integration and hardware test. Safety/BMS/inverter limits are not GUI knobs.
