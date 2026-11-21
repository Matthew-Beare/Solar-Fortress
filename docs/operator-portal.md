# Solar Fortress operator and Commander controls (STAGED, NOT DEPLOYED)

The Pi's running Venus OS controller contains newer maintenance fixes than
the public GitHub baseline. Never replace it with this feature branch without
inspecting live files and testing the actual hardware. All controls below are
source code and offline tests, NOT proof of inverter actuation.

## Access model

### First-run superuser enrollment (127.0.0.1:8082)

The Pi administrator settings site runs only on loopback behind the trusted
administrator's SSH tunnel. It does NOT bind to the public internet.

When the operator-auth.json file does not exist, the first visit to the root
page shows a Create First Superuser form with:
- chosen username (1–64 characters, NO preselected login);
- password field (8–256 characters, NOT an eight-character maximum);
- retype/confirm password field.

Two entered passwords must match. The credentials are hashed using salted
scrypt, never written in plaintext. On successful first registration, the
enrollment route is permanently closed while the account file exists.
All subsequent settings visits require that chosen name and password.
Password rotation requires the current password plus the new password
entered TWICE. Any migration/recovery must be explicitly handled by a
trusted administrator, not by making the registration URL public.

Start against a test copy using the serve action of src/operator_portal.py
with --config /test/controller.json --auth /test/operator-auth.json.
There is no hard-coded account/password or required CLI init-password flow.
This web login is only a barrier against accidental settings changes; it
is NOT the authentication for GREPmox-to-Pi storm plans or Linux root.

### Commander Boomer no-login control (staged 127.0.0.1:8083)

Commander must NOT need to log in for a manual full-charge request.
src/commander_portal.py stages a separate private-page implementation.
It has two kinds of narrowly scoped controls:
- Request Full Charge: choose how many hours to hold FULL after an actual,
  NEW SmartShunt full-charge event; explicit checkbox confirmation required.
  Default 12 hours, max 168 hours, both admin editable.
- Cancel Full Charge Request: stops only that manual intervention and returns
  the mode decision to existing local/server policy.
- Manual Storm Mode: select temporary storm charging, temporary storm
  pass-through, force the existing normal SOC policy, or cancel manual mode.
  Each requires an explicit confirmation and a selected 1–168 hour expiry
  within the admin-configured maximum (default duration 24 hours).
  Repeating the same mode does NOT silently renew its expiry; Commander
  must check the deliberate renewal box.

The POST forms use anti-CSRF tokens. Another request cannot silently reset
or extend an active hold; Commander must cancel it or check the explicit
replace-existing-request box. This prevents accidental double taps.

The no-login page must be embedded in the normal dashboard or assigned an
explicit trusted private-LAN bind address at deployment. Never port-forward
this interface to the internet or give it general BMS/inverter settings access.
No general non-admin account or password is required. The no-login
UI is accessible only on the deliberately configured private network;
the administrator GUI remains separate and password protected.

src/commander_full_charge.py stores a one-shot change in a local file:
- Requesting full charge immediately selects the Pi's local charging advisory.
  It has NO arbitrary target deadline to attain 100% SOC.
- The manual override itself carries an absolute failsafe expiration,
  default 168 hours (7 days), adjustable 1–720 hours by the superuser.
  This stops stale commands if SmartShunt never confirms full.
- The user-selected hold begins at the verified full charge, not button press.
- The hold expires automatically; 0 hours means finish immediately at full.
- Old full-charge timestamps and server loss cannot fake a new full charge.
- State changes persist across reboot. Repeated status polling does not write
  periodically to the Pi microSD. All outputs are READ-ONLY ADVISORIES.

The physical Pi controller electrical actuator and embedding in the existing
port 8081 dashboard are NOT yet implemented. This branch cannot start a
real charge.

### Every storm override expires

src/pi_storm_supervisor.py requires absolute expiry times on local manual
Storm Mode overrides, including forced charge and forced pass-through.
Default local manual duration is 24 hours; maximum is 168 hours, both
editable in the superuser GUI. Explicit renewal is necessary to extend.

GREPmox plans also have issued_at and expires_at; they expire by default
after 300 seconds unless refreshed with a new authenticated plan. Expired
manual/storm plans fall back to the existing Pi normal SOC policy, unless
a separate still-valid local override is in force.

The manual full-charge request has its OWN failsafe lease expiry and a
separate post-full hold expiry. Actual SmartShunt full resets its active
expiration to full-event time plus the selected hold duration. It takes
precedence over a valid VM storm recommendation but cannot override
inverter AC-loss handling, BMS, SOC safety limits, or hardware rules.

## Other adjustable settings

The protected GUI validates and persists:
- SOC charge/pass-through/return thresholds and qualification timers;
- maintenance full-charge cadence, retries and post-full balancing hold;
- SmartShunt learning, history retention, SOC matching, taper-current
  settings, conservative multiplier, battery bank capacity and planning rate;
- Storm Watch periods, forecast confirmation, snow/ice/wind thresholds,
  weather polling, freshness, NWS point and winter alerts;
- charge-time safety buffer, storm recovery duration, event joining,
  storm target/reserve SOC and server heartbeat expiry;
- Commander full-charge enable, default/max post-full hold and manual
  override failsafe expiration (default 168 hours, max 720 hours).

The ordinary settings can be changed without manually editing source code.
BMS, hardware current/wiring constraints and protections cannot be disabled
through the GUI.

## Canonical server/Pi split

The chosen production design is an offline Raspberry Pi with VM102 owning
forecast/charging-history learning and issuing only short-lived, authenticated
private-LAN plans. The Pi controls Victron hardware and returns to normal SOC
policy if GREPmox disappears. The earlier continuous Pi weather-polling
prototype is superseded and cannot run in loop mode.

No authenticated VM-to-Pi transport, real admin enrollment, Commander-facing
dashboard control or electrical charge actuator is deployed merely because
the source simulations pass. Validate the installed code, reboot persistence,
server outage and real Victron transitions before enabling physical actions.
