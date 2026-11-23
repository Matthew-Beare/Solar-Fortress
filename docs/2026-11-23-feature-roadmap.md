# Solar Fortress feature roadmap and reconciliation checkpoint — 2026-11-23

**Status:** planning and requirements only. No controller code, hardware behavior, services, accounts, notifications, or operating settings are changed by this document.

**Feature register:** [Solar Fortress Native Implementation Tracker](https://docs.google.com/spreadsheets/d/1NmQLPSVt8Y6u1Wwhk-g7KJhgzZS-WfsTd5269Up2nqM/edit), rows F114–F157, plus revised legacy entries and tests T12–T21.

## Provenance and source reconciliation (blocking before UI deployment)

The user's November 23 screenshot of the actual Pi at `192.168.1.21:8080` shows the newer dark **Solar Fortress Energy Command Center** with Overview / Live Analytics / System Health, animated flow and premium cards. Current public `main` has the October 19 initial-release `src/web_dashboard.py`. The listed feature branches `feat/solar-storm-operator-20261121` and `fix/mission-control-led-rendering` did not provide the photographed Energy Command Center strings when inspected on Nov 23. Therefore **neither the public main nor a found branch is established as the deployed UI source**. Do not replace a live Pi file with main.

Before implementation: obtain a read-only, timestamped manifest of running Pi files and service command lines, SHA-256 hash and copy the deployed `src/web_dashboard.py`, controller, hardware adapter, runtime, CSS/JS, config, startup hooks and event/history mechanisms to safe staging; inspect diffs versus all candidate repository refs and any local unstaged workstation checkout. Confirm which version owns UI 8081 versus controller API 8080 before changes. Preserve current Venus OS controller, LED and energy-flow behavior; regression-test screenshots and actual hardware readbacks. There is no claim of live SSH access or reconciliation in this document.

The older Google Drive feature register contains proposals for TimescaleDB and years of **every** one-second metric; as of November 2026, `boomer-HQ` documents *deployed* InfluxDB 2 + Grafana + Prometheus on VM102, with live Pi polling still unverified on the staged network. The newer request is **selective raw retention**, while preserving one-second sampling and useful long-term core metrics. Do not infer that proposed cloud/replica features are deployed.

## Primary GUI status

Keep the existing Energy Command Center visual design. Directly beneath health indicators, show a dominant, mobile-visible operating status:

- Current **observed** state (battery-first / pass-through / grid charging / storm or manual effects); human-readable normal/warning/fault classification.
- **WHY** the current state was entered, from the authoritative controller transition record rather than an inference from current SOC; previous state, policy/override source, threshold and qualification.
- **IN THIS STATE FOR: hh:mm:ss** running every second, backed by durable transition timestamp and reboot-safe identity; separate clocks for each active manual/storm latch and for pending qualification.
- Exact timestamp in `America/New_York` plus next condition to change modes, effective adaptive return target, qualification status and any valid hold/override expiration.
- Show stale/unknown data as unknown, never healthy green. Deliberate `IGNORE AC` while battery-first/inverting is normal; physical grid outage is a separate observed event.
- Display active storm/manual/maintenance/fault latches across the top **only while active**, each with operator/source, reason, target, start time, absolute expiry and countdown; require authorized confirmation to cancel/replace/renew.

Distinguish **requested**, **applied**, and **read-back observed** electrical mode, recording failures and disparities. No historical reason should be fabricated if the true triggering event was not retained.

## Adaptive anti-flapping policy (proposal, not yet approved for actuation)

The existing default SOC control policy remains 30% pass-through, 20% emergency grid charging and 35% battery-first return, with its existing qualification/safety behavior.

- After a normal pass-through → battery-first return at **35%**, start a configurable relapse window, suggested default **60 minutes**.
- If a new low-SOC transition back to pass-through occurs inside the window, raise the **next battery-first return threshold** to **40%**.
- If the battery again returns to battery-first and relapses within the configured window, raise the next return threshold to **50%**, the proposed upper limit.
- Optional elevated-return qualification: a proposed **30 minutes of continuously meeting the elevated SOC target**, adjustable or disable-able; never impose it in ways that bypass electrical safety.
- Escalation steps, max, relapse time, qualification, hysteresis and post-stability reset period are editable through the superuser GUI, with validation and an event trail.
- Persist the stage, triggering times and target over Pi restart; surface on the banner and in logs. Avoid duplicate events on reboot.
- **Important:** Raising the battery-first return threshold does **not** authorize grid charging. During nighttime pass-through, SOC may not recover to 40–50% until solar becomes available. The UI must explain a prolonged pass-through instead of introducing accidental AC charging.
- Preserve 20% emergency grid charging, SmartShunt maintenance behavior, hard BMS/inverter/wiring limits and normal control priority. Hardware-test every transition before enabling new actuation.

## Manual and storm controls

- Commander operates local no-login controls; the superuser web GUI has separate authenticated privileges.
- For high-impact manual overrides, require explicit action preview and an action-scoped **PIN** (not necessarily account login). For especially consequential actions, add an exact typed confirmation phrase; prevent accidental touch-only activation, lock/rate-limit failed attempts, and journal every attempt.
- One-time first-run superuser enrollment: user-selected username and password typed twice, **8–256 characters**; close enrollment after the first account is made.
- Commander may request **immediate** full charge without an arbitrary deadline for *reaching* full. On a **NEW SmartShunt-confirmed full event**, begin the selected hold (default **12 hours**); set the override expiry to confirmed-full time + selected hold. Before full, an absolute failsafe (default **7 days**, configurable **1–720 hours**) prevents stuck commands. Display both waiting-for-full and post-full hold phases.
- Commander temporary controls: Storm Charging, Storm Pass-Through, Normal, Automatic, cancel; finite local absolute expiry, confirmation for replacement/renewal/cancel, and explicit time remaining.
- VM102 retrieves forecasts and sends authenticated private-LAN storm plans with short absolute expiry and renewal. A plan expiring or GREPmox failing causes the Pi to resume normal SOC policy unless a valid local manual override exists.
- Severe-weather forecast qualification does not require historical evidence; learn actual charging time through SmartShunt-confirmed full including taper. Default forecast safety buffer **4 hours** and storm recovery period **12 hours** after latest relevant forecast accumulation.
- Every ordinary operating threshold, timer, forecast parameter, telemetry interval, learning variable and supported override duration is editable through authenticated GUI, with typed ranges/order validation and atomic audit. GUI must never bypass electrical protections.

## Event journal, monitoring and subscriber-specific alerts

- Store source-timestamped, structured, append-only-ish Pi events: mode request, hardware readback, exact reason, actor, overrides, alarm and recovery, SmartShunt full, MPPT anomalies, telemetry loss, grid availability, storm plan/expiry, config revisions and failed actions.
- New **Logs** tab on the Pi, functional without VM102; filter/search and event detail with timestamps, SOC/AC/Victron context, threshold, reason, successful or failed control readback and links to relevant Grafana historic interval when reachable.
- Persist high-value events longer than repetitive state polls; redact credentials. Log transport attempt/acknowledgment separately from underlying events.
- Independent per-person alert settings for owner and Commander: per-alert opt-in, severity, individual **notification** threshold and repeat interval, quiet hours, escalation and channel. One system-wide **electrical** threshold set remains authoritative.
- Commander may want every **meaningful** transition with exact WHY; neither person needs endless one-second repeats. Deduplicate flaps while retaining each event; rate-limit notifications without suppressing critical local indicators. Charging start/stop notifications are optional, not mandatory.
- Signal preferred using actively maintained, **unofficial** `signal-cli` plus a tested API bridge without requiring a spare phone device. Validate linking, identity, reboot, upgrade, delivery failure, and maintenance. Do not assume official Signal bot support.
- Telegram is the simpler officially supported fallback: send Bot API `sendMessage` through standard outbound HTTPS; **no Telegram application or daemon needs to run on the Pi**.
- **Pi has no public internet access.** To deliver from Pi when VM102/GREPmox fails, Pi may push an authenticated, bounded message over private LAN to an **independent powered outbound relay** (e.g. router/OpenWrt or another proven surviving endpoint); that relay sends Telegram. A Signal bridge on VM102 alone does not survive GREPmox failure. Test router/modem/ONT, WAN, switching and backup power before promising alerts during utility loss.
- Independent offsite heartbeat must watch for whole-site/Pi silence; the failed Pi cannot report its own outage.
- Preserve local electrical autonomy whether every alert path succeeds or fails.

## Data retention, calibration and history

- Keep **one-second** timestamped Victron measurements/collection, quality and staleness flags, bounded buffering, idempotent backfill, and no fabricated samples. Heavy ingestion belongs on VM102.
- Select raw retention by metric: battery SOC/current/voltage/power, solar PV/energy and electricity use are candidates for longer retention; repetitive AC-present boolean polls may be kept briefly (e.g. 30–90 days) plus long-lived **transitions** and compact aggregate coverage. Final budgets and policies adjustable through superuser GUI; preserve data needed for valid comparisons.
- Existing InfluxDB on VM102 has 30-day bucket retention; any increase requires observed SSD free space, query/load testing and a deliberate backup/archival plan. Keep useful long-term events and aggregated historical data off the Pi. Later ship selected verified history and event logs to WREK HQ with dedupe/checkpoint/integrity checks.
- Use the first **~30 days** of measured solar output paired with recorded forecast issuance for an empirical forecast-to-production efficiency calibration. Continue updating when evidence is representative, with uncertainty and seasonal weighting; do not assume one month's conditions cover all seasons.
- Exclude nights, battery-full charge curtailment, MPPT limiting, outages, shading/snow and stale data when estimating equipment efficiency. Forecast processing and model comparison belong to GREPmox/VM102; do not put weather internet on Pi.
- Open-Meteo historical forecast runs and Solcast are candidates to evaluate based on terms/limits. Forecast refresh cadence need not be 1Hz.

## GUI navigation

Preserve Overview, Live Analytics, System Health. Add role-aware **Logs**, **Alerts**, **Settings**, **Operations**, and **Integrations**. Links to Grafana/Prometheus/InfluxDB must route through appropriate authenticated access; current guest loopback-only `127.0.0.1` listeners cannot be linked unmodified from another phone.

## Verification gates

No automatic electrical actuation until (1) deployed source reconciled and preserved, (2) Pi restart and clock uncertainty tested, (3) actual 35→40→50 relapse scenarios verified, (4) 20% safety/maintenance safeguards verified, (5) override PIN and full-event/expiry flows tested, (6) server/WAN/power-loss emergency alert tested, (7) observer/readback state confirmed, and (8) one-second database/backfill and link security tests pass. Spreadsheet **Critical Tests** T12–T21 tracks these.

## Hardware note: 2/0 cable manifold retainers

The six-cable 2/0 sweep manifold and removable PETG/ASA retainer design should avoid compressing insulation and respect cable bend radius, cable heat and serviceability. Prototype **M3 x 5 mm OD x 4 mm length** Voron-style brass heat-set inserts with matching **M3 x 8–12 mm** screws sized to clamp thickness; use manufacturer-specific pocket sizes and test pullout before selecting final BOM. These are **non-structural** cable retainers, not electrical terminal components.
