# Solar Fortress: server planning, local control (canonical design)

Status: staged feature branch only. Not merged, deployed, actuating the MultiPlus-II,
or verified against the newer running Pi controller. Earlier Pi-only architecture,
including continuous Pi-side weather fetching and charge-history collection,
is SUPERSEDED by this decision.

## Physical trust and storage boundary

- Raspberry Pi 4 running Venus OS: NO INTERNET EGRESS. Allow only strictly
  required private-site LAN communication with the Boomer HQ server and local
  trusted administration. Enforce this with firewall/network rules, not merely
  by declining to call a web API. The Pi alone owns Victron electrical control.
- GREPmox / boomer-solar VM102: fetch NWS and active alerts, store forecast
  revisions, collect Pi read-only SmartShunt telemetry, learn true
  time-to-full-charge from completed charging sessions including taper,
  estimate the storm deadline, and issue a short-lived private-network plan.
- Storage: GREPmox has an SSD for its OS and a separate HDD for bulk storage.
  Do not infer that a VM database currently resides on either disk; verify
  its actual backing volume before deploying the high-write history. Use
  appropriately batched database writes on the server's selected datastore.
  Pi persistence should be limited to small configuration and MANUAL override
  state on changes, not periodic forecast or per-minute learning history.
- Pi local controller may read the VM's plan over an authenticated LAN path
  after that transport is implemented. No unrestricted or raw Victron command
  API may be granted to VM102.

## Server failure / normal fallback

The Pi expects an authenticated short-lived storm plan, version 1, containing
issued_at, expires_at and an allowlisted mode: normal, storm_charge or
storm_pass_through. If the server dies, plan authentication fails, or the
server plan expires, the automatic override is removed. The Pi returns to
its PRE-EXISTING NORMAL SOC policy, including local AC-loss and battery
protection rules. The normal fallback does NOT retain an endless server
weather latch. Default server-plan grace is 300 seconds, configurable
15 to 3600 seconds via the protected administrator settings.

The Pi can always operate safely without the VM, internet, NWS, or historical
database. The tradeoff is that normal mode may choose battery-first while a
storm is still ongoing, potentially consuming reserve. This is an EXPLICIT
user-selected availability tradeoff, not a bug to silently override.

## Commander Boomer's manual override

Pi-local manual choices are automatic, normal, storm_charge, and
storm_pass_through. The explicit local choice outranks an existing fresh
server plan and works when the VM is unavailable. The change is stored with
one atomic local state write and read back after reboot. Selecting the same
mode again performs no write. Every change requires a confirmed local action.
All manual choices remain subordinate to local battery/BMS, AC source,
Victron hardware, and wiring limits.

src/pi_storm_supervisor.py now implements and tests this read-only policy.
The actual Commander-facing web control and electrical command integration
are NOT YET WIRED TO THE DEPLOYED Pi. Do not claim the override works on the
physical inverter just because its pure state machine tests pass.

## Local operator password is an accidental-edit guard

The staged localhost-only (127.0.0.1:8082) settings editor now accepts a
custom administrator username, e.g. Captain Jean-Luc Picard, and an 8+
character password. Credentials are chosen locally at initialization,
salted and hashed; never commit actual credentials. User-chosen weak or
themed codes are permissible solely under this deliberate LOCAL TRUST model.
Do NOT expose port 8082 to the internet or untrusted LAN. This password
does not authenticate VM102 storm-control messages, grant Linux root access,
or protect the Victron BMS. Server plan messages need independently
authenticated transport before any physical actuation.

## Charging and storm logic retained on the server

The 4-hour charging safety margin, 12-hour post-last-accumulation hold,
ice/snow/wind thresholds, time windows, polling, taper-learning settings,
maintenance hold, and other normal operating parameters remain independently
configurable. GREPmox should continuously compute the learned time to actual
SmartShunt full charge and use:

    charging deadline = learned remaining hours + configured storm buffer

After the forecasted accumulation and the independently configured recovery
window have elapsed, VM102 can propose returning to normal. If VM102 fails,
the Pi instead invokes the normal-mode fallback described above. Commander
can override storm mode locally when necessary.

The earlier proof-of-concept modules src/charge_monitor.py,
src/charge_learning.py, src/nws_client.py, src/storm_watch.py,
src/storm_latch.py and src/operator_cycle.py remain for reuse/refactor,
but Pi-side automatic looping is disabled in operator_cycle.py. They are
NOT the selected production scheduling architecture.

## Deployment and acceptance gates

1. Confirm the physical Pi/Venus OS installed files, preserve current
   SmartShunt 60-minute maintenance hold and 20% emergency protections.
2. Confirm VM102 actual HDD vs SSD backing storage and telemetry paths;
   make server read-only collection and storm learning work there.
3. Implement authenticated VM102 plan delivery, Pi heartbeat/freshness
   checks, replay resistance, and local electrical safety precedence.
4. Add a dedicated, confirmed Commander manual override to the Pi local
   GUI. Keep the ordinary display view-only otherwise.
5. Independently test four scenarios: healthy VM/storm, server loss during
   charging, server loss during storm/pass-through, and local manual override
   while server and internet are down.
6. Verify that exactly the prior normal SOC behaviour resumes after the
   300-second default timeout. Test alternative timeout values, Pi reboot,
   grid loss, return of AC and restoration of VM plan after failover.
7. Only after local hardware tests, enable actual inverter transitions.

GitHub Actions success means unit tests passed, not that field deployment
or any Victron electrical action occurred.
