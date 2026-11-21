"""Official NWS read-only forecast and active winter-weather alert fetcher.

This self-contained Pi adapter deliberately makes no inverter writes. Its
normalized forecast format matches the boomer-HQ grid adapter, while local
fetching lets the Pi continue evaluating even when VM102 is unavailable.
Coordinate settings must be supplied by the operator; never guessed.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import tempfile
import urllib.parse
import urllib.request

UTC = dt.timezone.utc
USER_AGENT = "Solar-Fortress/0.1 (local-read-only-energy-controller)"
MAX_BODY = 2 * 1024 * 1024
DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")
ALERT_TERMS = ("winter", "ice", "freez", "snow", "blizzard",
               "wind", "sleet", "freezing rain", "cold", "frost")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Refusing NWS HTTP redirect")


def get_json(url):
    target = urllib.parse.urlsplit(url)
    if (target.scheme != "https" or target.hostname != "api.weather.gov"
            or target.username or target.password):
        raise ValueError("Only official NWS HTTPS data is allowed")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect()
    )
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/geo+json",
    })
    with opener.open(request, timeout=12) as response:
        if response.status != 200:
            raise ValueError("Unexpected NWS HTTP status")
        raw = response.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        raise ValueError("Oversized weather response")
    return json.loads(raw.decode("utf-8"))


def _span(raw):
    try:
        beginning, duration = raw.split("/", 1)
        start = dt.datetime.fromisoformat(beginning.replace("Z", "+00:00"))
        match = DURATION.fullmatch(duration)
        if start.tzinfo is None or match is None or not any(match.groups()):
            raise ValueError("Invalid NWS forecast interval")
        length = dt.timedelta(
            days=int(match.group(1) or 0),
            hours=int(match.group(2) or 0),
            minutes=int(match.group(3) or 0),
        )
        if length.total_seconds() <= 0:
            raise ValueError("Invalid NWS duration")
        return start.isoformat(), (start + length).isoformat()
    except (AttributeError, ValueError) as exc:
        raise ValueError("Invalid NWS forecast validTime") from exc


def _unit(value, unit, kind):
    if (isinstance(value, bool) or not isinstance(value, (float, int))
            or not math.isfinite(value) or value < 0):
        raise ValueError("Invalid NWS numeric weather value")
    conversion = {
        "amount": {"wmoUnit:mm": 1 / 25.4, "wmoUnit:m": 39.37007874},
        "gust": {"wmoUnit:km_h-1": .6213711922, "wmoUnit:m_s-1": 2.2369362921},
    }
    if unit not in conversion[kind]:
        raise ValueError("Unsupported NWS weather unit " + str(unit))
    return round(value * conversion[kind][unit], 5)


def normalize_grid(grid):
    fields = (("snowfallAmount", "snow_inches", "amount"),
              ("iceAccumulation", "ice_inches", "amount"),
              ("windGust", "wind_mph", "gust"))
    properties = grid.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("NWS grid lacks properties")
    issue = properties.get("updateTime")
    if not isinstance(issue, str):
        raise ValueError("NWS grid issue time unavailable")
    stamp = dt.datetime.fromisoformat(issue.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("NWS issue time lacks timezone")
    periods, present = [], {}
    for source, key, unit_kind in fields:
        section = properties.get(source)
        present[key] = False
        if not isinstance(section, dict) or not isinstance(section.get("values"), list):
            continue
        unit = section.get("uom")
        for item in section["values"]:
            if item.get("value") is None:
                continue
            start, end = _span(item["validTime"])
            value = _unit(item["value"], unit, unit_kind)
            periods.append({"start": start, "end": end, key: value})
            present[key] = True
    return {"source": "NWS forecastGridData",
            "source_update_time": stamp.isoformat(),
            "fields_available": present, "periods": periods}


def active_winter_alerts(payload, now):
    """All winter/ice/snow/wind active alerts must clear before default release.

    Unrecognized malformed responses are errors, never evidence of safety.
    An empty valid active-alert feature list means clear.
    """
    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError("NWS active-alert response lacks features")
    active = []
    for item in features:
        if not isinstance(item, dict) or not isinstance(item.get("properties"), dict):
            raise ValueError("Malformed NWS alert")
        alert = item["properties"]
        event = alert.get("event")
        if not isinstance(event, str):
            raise ValueError("Missing NWS alert event")
        if not any(term in event.lower() for term in ALERT_TERMS):
            continue
        active.append(event)
    return {
        "checked_at": now.isoformat(),
        "alerts_clear": not bool(active),
        "active_winter_events": sorted(set(active)),
    }


def fetch_weather(latitude, longitude, now=None, fetch=get_json):
    if (isinstance(latitude, bool) or isinstance(longitude, bool)
            or not isinstance(latitude, (float, int))
            or not isinstance(longitude, (float, int))
            or not math.isfinite(latitude) or not math.isfinite(longitude)
            or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
        raise ValueError("A verified latitude/longitude pair is required")
    now = now or dt.datetime.now(UTC)
    location = str(latitude) + "," + str(longitude)
    info = fetch("https://api.weather.gov/points/" + location)
    url = info.get("properties", {}).get("forecastGridData")
    if not isinstance(url, str):
        raise ValueError("NWS /points did not provide forecastGridData")
    # Even injected/test callbacks must not be able to use an arbitrary host.
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "api.weather.gov":
        raise ValueError("Unexpected NWS grid endpoint")
    forecast = normalize_grid(fetch(url))
    alerts = active_winter_alerts(
        fetch("https://api.weather.gov/alerts/active?point=" + location),
        now,
    )
    forecast["retrieved_at"] = now.isoformat()
    return forecast, alerts


def write_atomic_json(path, obj):
    target = Path(path)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError("Unexpected NWS output path")
    if not target.parent.is_dir():
        raise ValueError("NWS output directory does not exist")
    handle, tmp = tempfile.mkstemp(prefix=".nws-", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            os.fchmod(out.fileno(), 0o600)
            json.dump(obj, out, separators=(",", ":"), allow_nan=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
