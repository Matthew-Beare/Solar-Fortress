# Mission Control UI 0.2.7

This replaces the v0.2.6 dashboard HTML after that release has been successfully installed. The charging policy is outside this UI update.

The v0.2.6 LED bug was CSS specificity: the class stylesheet forced a dark fill and 0.6 opacity, overriding the SVG presentation attributes set by JavaScript. Its reversed symmetric blink animation also failed to distinguish inverse blinking. The old checks searched the HTML for strings and did not check rendered colours.

The replacement uses a responsive HTML electrical map, a blue cabinet with a dark two-column indicator panel, eight readable labels, and explicit lit/off/unavailable states. It removes the invented LCD. The existing API, graphs and SQLite history integration remain in place.

## Verified front-panel layout

Victron's **MultiPlus-II 120V manual, section 3.4**, shows these eight indicators:

| Left column: charger | Right column: inverter |
| --- | --- |
| mains on — green | inverter on — green |
| bulk — yellow | overload — red |
| absorption — yellow | low battery — red |
| float — yellow | temperature — red |

Primary reference: https://www.victronenergy.com/media/pg/MultiPlus-II_120V/en/operation.html

Each indicator reads its individual key in the existing API's vebus_leds object. Values 0/1/2/3 select off/on/blink/inverse-blink; unavailable or invalid values are shown as unavailable. The blink animation represents the reported indication; it does not claim frame-level synchronization with the physical lamp oscillator.

The operating label uses observed VE.Bus LEDs. The selected controller mode remains a separate diagnostic. Grid presence, accepted AC and grid watts remain separate readings.

## Installation

Connect 2BOOMERHQ. In **Windows PowerShell**, expand SolarFortress-v0.2.7-UI-LED-FIX.zip and run SolarFortress-v0.2.7\install.ps1.

The installer:

1. Verifies checksums and runs the Python deployment tests.
2. Uses an isolated Edge/Chrome profile to check actual rendered colours, blink/inverse-blink indication and label bounds at 320, 390, 820, 1280 and 1920 CSS pixels, with five telemetry fixtures.
3. Aborts before uploading if a rendering check fails or is incomplete. The file render-verification-result.html contains the report.
4. Uploads exactly the tested HTML, manifest and UI deployer.
5. Checks the known live v0.2.6 source hashes and live API, backs up the current HTML, and changes the versioned UI symlink.
6. Confirms the served HTML checksum, eight live LED fields, unchanged controller/web PIDs, protected source hashes and SQLite identity. A failed readback restores the previous UI.

The Pi confirmation is:

    === VERIFIED: SOLAR FORTRESS UI 0.2.7 SERVED ===

This confirms the served UI and live data contract. Compare the displayed indicators with the physical inverter after refreshing the page with Ctrl+F5.

The update does not restart services or write to Victron D-Bus, the charging policy, configuration or database. It refuses unexpected live revisions.

## Offline review and regression

Open preview.html for a labelled simulated preview. Open verify_render.html in a browser to run the rendered tests manually. Neither file contacts the Pi.

Rebuild generated files from the repository root:

    python mission-control/build_release.py

Run:

    python -m unittest discover -s mission-control/tests -v
    node mission-control/tests/test_led_logic.cjs
    python mission-control/tests/render_browser.py

The last command requires Chrome/Chromium and saves test reports and three rendered previews under mission-control/render-results.
