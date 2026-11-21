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
server plan and works when the VM is unavailable. EVERY manually selected
storm override now includes an absolute expiration timestamp. The default
manual lifetime is 24 hours and the maximum 168 hours, both configurable
through the superuser GUI. A legacy or expired manual entry is ignored and
the Pi resumes the current server-plan or normal SOC policy. Changes persist
across reboot; deliberately renewing requires explicit confirmation and a
new expiration, rather than silently extending on duplicate clicks.
All manual choices remain subordinate to local battery/BMS, AC source,
Victron hardware, and wiring limits.

Commander Boomer can also request a one-time full charge WITHOUT an account
or login. His no-login page asks how many hours the battery should be held
full AFTER a NEW SmartShunt full-charge event, requires confirmation, and
provides a cancellation button. The same no-login trusted-LAN page
now provides temporary local storm charge, storm pass-through, normal,
and cancel-manual-override options. Each local storm selection asks for
a duration (1 to 168 hours, subject to the superuser-configured maximum),
requires explicit confirmation, and shows its absolute expiration. Renewing
an existing manual mode requires a separate explicit renewal checkbox.
The request immediately asks the Pi to grid-charge locally, without
an arbitrary deadline by which it must reach full. The manual override
still carries an absolute failsafe expiration (168 hours / 7 days by
default, adjustable 1–720 hours), preventing a stale command from lasting
forever if SmartShunt never confirms full. Once SmartShunt confirms the
new full event, the expiry becomes that actual event plus Commander's
selected post-full hold (0–168 hours). The default hold is 12 hours,
and both hold limits and the failsafe expiry are superuser-configurable. An existing request
cannot be silently replaced/double-clicked: explicit replace or cancel is
required. This local request takes precedence over a server storm plan but
cannot bypass any Pi hardware safeguard. The request state machine and
private no-login page are staged, not wired into the actual inverter or the
existing ordinary dashboard yet.

src/pi_storm_supervisor.py now implements and tests this read-only policy.
The actual Commander-facing web control and electrical command integration
are NOT YET WIRED TO THE DEPLOYED Pi. Do not claim the override works on the
physical inverter just because its pure state machine tests pass.

## Local operator password is an accidental-edit guard

The staged localhost-only (127.0.0.1:8082) settings editor now uses a
first-run registration screen: the superuser CHOOSES the username and enters
a password TWICE. No login is hardcoded or pre-enrolled. Passwords may be
8 to 256 characters; eight is a minimum, never a maximum. Enrollment is
disabled after the first account is registered. Password rotation also
requires current password plus new password twice. The salted password hash
is stored locally and never committed. This admin credential is deliberately
separate from the private-LAN server plan authentication. No-login Commander
actions have only the single full-charge/cancel scope.
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
4. Embed the staged Commander no-login full-charge/hold/cancel action
   in the ordinary Pi dashboard without exposing administrator settings.
   Connect SmartShunt event updates and independently test completion,
   timeout, cancellation and double-submit behaviour.
5. Independently test four scenarios: healthy VM/storm, server loss during
   charging, server loss during storm/pass-through, and local manual override
   while server and internet are down.
6. Verify that exactly the prior normal SOC behaviour resumes after the
   300-second default timeout. Test alternative timeout values, Pi reboot,
   grid loss, return of AC and restoration of VM plan after failover.
   Separately test Commander local Charge Now while VM102 is down, arrival
   of the SmartShunt full event long after the old 72-hour assumption,
   a selectable post-full hold, cancellation, and expiry of a stale local
   manual command without any valid full event.
7. Only after local hardware tests, enable actual inverter transitions.

GitHub Actions success means unit tests passed, not that field deployment
or any Victron electrical action occurred.
