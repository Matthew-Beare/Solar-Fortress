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
- Storm Mode enabled/disabled, forecast lookahead (default 12 hours)
- Per-condition snow (6 in), ice (0.25 in), wind (disabled by default)
- Storm target 100%, storm reserve 90% stored as policy defaults

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
