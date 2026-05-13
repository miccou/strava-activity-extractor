import json
import re
import uuid
import time
from datetime import datetime, date

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)


# ---------------------------------------------------------------------------
# cURL parsing
# ---------------------------------------------------------------------------

def parse_curl(curl_string: str) -> tuple[str, dict]:
    """Return (url, headers_dict) extracted from a cURL command string."""
    # Collapse multiline continuations
    curl_string = re.sub(r"\\\s*\n\s*", " ", curl_string.strip())

    url_match = re.search(r"curl\s+'([^']+)'", curl_string) or re.search(
        r'curl\s+"([^"]+)"', curl_string
    )
    if not url_match:
        raise ValueError(
            "Could not find a URL. Make sure the command starts with: curl 'https://...'"
        )

    url = url_match.group(1)
    headers: dict[str, str] = {}

    for m in re.finditer(r"-H\s+'([^']+)'", curl_string):
        raw = m.group(1)
        if ": " in raw:
            k, v = raw.split(": ", 1)
            headers[k] = v

    for m in re.finditer(r'-H\s+"([^"]+)"', curl_string):
        raw = m.group(1)
        if ": " in raw:
            k, v = raw.split(": ", 1)
            headers[k] = v

    if not any(k.lower() == "cookie" for k in headers):
        raise ValueError(
            "No Cookie header found. Copy the full cURL command including the Cookie header."
        )

    return url, headers


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _get(d: dict, *keys):
    """Return the first non-None value found among the given keys."""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def fmt_duration(secs) -> str:
    if secs is None:
        return "N/A"
    try:
        secs = int(float(secs))
    except (TypeError, ValueError):
        return "N/A"
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_pace(speed_ms, sport: str = "") -> str:
    try:
        speed_ms = float(speed_ms)
    except (TypeError, ValueError):
        return "N/A"
    if not speed_ms or speed_ms <= 0:
        return "N/A"
    sl = sport.lower()
    if any(x in sl for x in ["ride", "cycle", "bike", "ski", "skate", "row"]):
        return f"{speed_ms * 3.6:.1f} km/h"
    if "swim" in sl or "openwater" in sl:
        s = 100 / speed_ms
        return f"{int(s) // 60}:{int(s) % 60:02d} /100m"
    s = 1000 / speed_ms
    return f"{int(s) // 60}:{int(s) % 60:02d} /km"


def fmt_dist(meters, sport: str = "") -> str:
    if meters is None:
        return "N/A"
    try:
        meters = float(meters)
    except (TypeError, ValueError):
        return "N/A"
    if "swim" in sport.lower() or "openwater" in sport.lower():
        return f"{int(meters)}m"
    return f"{meters / 1000:.2f} km"


def parse_act_date(act: dict):
    # Prefer Unix timestamp (web endpoint)
    raw_ts = act.get("start_date_local_raw")
    if raw_ts:
        try:
            return datetime.utcfromtimestamp(int(raw_ts)).date()
        except (TypeError, ValueError, OSError):
            pass
    raw = _get(act, "start_time", "start_date_local", "startDateLocal", "start_date", "startDate")
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(str(raw)[:25], fmt).date()
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Activity formatter
# ---------------------------------------------------------------------------

_LABEL_W = 9  # width of label field before ": "

def _row(label: str, value) -> str:
    return f"{label:<{_LABEL_W}}: {value}"


def format_activity(act: dict) -> str:
    lines = []
    sport = _get(act, "sport_type", "sportType", "type", "activityType") or "Activity"
    name = act.get("name", "Untitled")
    aid = act.get("id")

    lines.append(_row("Activity", name))
    lines.append(_row("Type", sport))
    if aid:
        url = act.get("activity_url") or f"https://www.strava.com/activities/{aid}"
        lines.append(_row("Link", url))

    # Date — prefer Unix timestamp, fall back to ISO string
    raw_ts = act.get("start_date_local_raw")
    raw_iso = _get(act, "start_time", "start_date_local", "startDateLocal")
    parsed_dt = None
    if raw_ts:
        try:
            parsed_dt = datetime.utcfromtimestamp(int(raw_ts))
        except (TypeError, ValueError, OSError):
            pass
    if not parsed_dt and raw_iso:
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                parsed_dt = datetime.strptime(str(raw_iso)[:25], fmt)
                break
            except ValueError:
                pass
    if parsed_dt:
        lines.append(_row("Date", parsed_dt.strftime('%a %d %b %Y, %I:%M %p')))

    # Distance — prefer *_raw (meters); plain field is pre-formatted string in user units
    dist = _get(act, "distance_raw", "distance_in_meters")
    if dist is None:
        # Try plain field but only if long_unit indicates meters
        d_plain = act.get("distance")
        if d_plain is not None:
            try:
                d_val = float(d_plain)
                long_unit = (act.get("long_unit") or "").lower()
                dist = d_val if "meter" in long_unit else d_val * 1000
            except (TypeError, ValueError):
                pass
    if dist is not None:
        lines.append(_row("Distance", fmt_dist(dist, sport)))

    # Times — prefer *_raw (seconds)
    mt = _get(act, "moving_time_raw", "moving_time_seconds", "moving_time", "movingTime")
    et = _get(act, "elapsed_time_raw", "elapsed_time_seconds", "elapsed_time", "elapsedTime")
    # If the value looks like a pre-formatted string (e.g. "35:56"), only use _raw
    def _is_numeric(v):
        try:
            float(v); return True
        except (TypeError, ValueError):
            return False
    if not _is_numeric(mt):
        mt = None
    if not _is_numeric(et):
        et = None
    if mt:
        lines.append(_row("Moving", fmt_duration(mt)))
    if et and et != mt:
        lines.append(_row("Elapsed", fmt_duration(et)))

    # Pace — from explicit speed fields or derived from distance/time
    avg_spd = _get(act, "average_speed", "averageSpeed")
    max_spd = _get(act, "max_speed", "maxSpeed")
    if not avg_spd and dist and mt:
        try:
            avg_spd = float(dist) / float(mt)   # m/s
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    if avg_spd:
        lines.append(_row("Avg Pace", fmt_pace(avg_spd, sport)))
    if max_spd and max_spd != avg_spd:
        lines.append(_row("Max Pace", fmt_pace(max_spd, sport)))

    # Elevation — prefer *_raw
    elev = _get(act, "elevation_gain_raw", "total_elevation_gain", "totalElevationGain", "elevationGain")
    if elev:
        try:
            lines.append(_row("Elev Gain", f"{float(elev):.0f}m"))
        except (TypeError, ValueError):
            pass

    avg_hr = _get(act, "average_heartrate", "averageHeartrate", "avgHeartrate")
    max_hr = _get(act, "max_heartrate", "maxHeartrate")
    if avg_hr:
        lines.append(_row("Avg HR", f"{float(avg_hr):.0f} bpm"))
    if max_hr:
        lines.append(_row("Max HR", f"{float(max_hr):.0f} bpm"))

    avg_watts = _get(act, "average_watts", "averageWatts")
    max_watts = _get(act, "max_watts", "maxWatts")
    if avg_watts:
        try:
            lines.append(_row("Avg Power", f"{float(avg_watts):.0f} W"))
        except (TypeError, ValueError):
            pass
    if max_watts:
        try:
            lines.append(_row("Max Power", f"{float(max_watts):.0f} W"))
        except (TypeError, ValueError):
            pass

    cals = _get(act, "calories")
    if cals:
        try:
            lines.append(_row("Calories", f"{float(cals):.0f} kcal"))
        except (TypeError, ValueError):
            pass

    suffer = _get(act, "suffer_score", "sufferScore", "relative_effort", "relativeEffort")
    if suffer:
        lines.append(_row("Effort", suffer))

    cadence = _get(act, "average_cadence", "averageCadence")
    if cadence:
        try:
            lines.append(_row("Cadence", f"{float(cadence):.0f} rpm/spm"))
        except (TypeError, ValueError):
            pass

    return "\n".join(lines)


def format_activity_compact(act: dict) -> str:
    sport = _get(act, "sport_type", "sportType", "type", "activityType") or "Activity"

    # Date
    raw_ts = act.get("start_date_local_raw")
    date_str = ""
    if raw_ts:
        try:
            date_str = datetime.utcfromtimestamp(int(raw_ts)).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            pass
    if not date_str:
        raw_iso = _get(act, "start_time", "start_date_local", "startDateLocal")
        if raw_iso:
            for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
                try:
                    date_str = datetime.strptime(str(raw_iso)[:25], fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    pass

    dist = _get(act, "distance_raw", "distance_in_meters")
    if dist is None:
        d_plain = act.get("distance")
        if d_plain is not None:
            try:
                d_val = float(d_plain)
                long_unit = (act.get("long_unit") or "").lower()
                dist = d_val if "meter" in long_unit else d_val * 1000
            except (TypeError, ValueError):
                pass

    mt = _get(act, "moving_time_raw", "moving_time_seconds")
    if not mt or not str(mt).replace(".", "").isdigit():
        mt = None

    avg_spd = _get(act, "average_speed", "averageSpeed")
    if not avg_spd and dist and mt:
        try:
            avg_spd = float(dist) / float(mt)
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    avg_hr = _get(act, "average_heartrate", "averageHeartrate", "avgHeartrate")

    parts = [sport]
    if date_str:
        parts.append(date_str)
    if dist is not None:
        parts.append(fmt_dist(dist, sport))
    if mt:
        parts.append(fmt_duration(mt))
    if avg_spd:
        parts.append(fmt_pace(avg_spd, sport))
    if avg_hr:
        try:
            parts.append(f"{float(avg_hr):.0f} bpm avg")
        except (TypeError, ValueError):
            pass

    return " | ".join(parts)


def format_all_compact(activities: list) -> str:
    if not activities:
        return "No activities found for the specified criteria."
    header = f"# Strava Activities — Compact ({len(activities)} total)\n"
    lines = [header]
    for act in activities:
        lines.append(format_activity_compact(act))
    return "\n".join(lines)


def format_all(activities: list) -> str:
    if not activities:
        return "No activities found for the specified criteria."

    header = f"# Strava Activities ({len(activities)} total)\n"
    blocks = [header]
    for i, act in enumerate(activities, 1):
        blocks.append(f"---\n### {i}.\n{format_activity(act)}\n")
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Strava fetching
# ---------------------------------------------------------------------------

def build_req_headers(base: dict) -> dict:
    h = dict(base)
    h["Accept"] = "application/json, text/javascript, */*; q=0.01"
    h["X-Requested-With"] = "XMLHttpRequest"
    return h


def fetch_activity_detail(activity_id: int, headers: dict) -> dict | None:
    """Fetch HR, cadence and watts from the streams endpoint."""
    req_h = build_req_headers(headers)
    extra: dict = {}

    try:
        r = requests.get(
            f"https://www.strava.com/activities/{activity_id}/streams",
            headers=req_h,
            params={"stream_types[]": ["heartrate", "cadence", "watts"]},
            timeout=20,
        )
        if r.status_code == 200:
            streams = r.json()
            if isinstance(streams, dict):
                stream_list = [
                    {"type": k, **v} if isinstance(v, dict) else {"type": k, "data": v}
                    for k, v in streams.items()
                ]
            elif isinstance(streams, list):
                stream_list = streams
            else:
                stream_list = []

            for stream in stream_list:
                stype = stream.get("type")
                data = stream.get("data") or []
                numeric = [x for x in data if isinstance(x, (int, float))]
                if not numeric:
                    continue
                if stype == "heartrate":
                    extra["average_heartrate"] = sum(numeric) / len(numeric)
                    extra["max_heartrate"] = max(numeric)
                elif stype == "cadence":
                    extra["average_cadence"] = sum(numeric) / len(numeric)
                elif stype == "watts":
                    extra["average_watts"] = sum(numeric) / len(numeric)
                    extra["max_watts"] = max(numeric)
        else:
            print(f"[streams {activity_id}] status {r.status_code}")
    except Exception as e:
        print(f"[streams {activity_id}] error: {e}")

    print(f"[detail {activity_id}] fields: {list(extra.keys())}")
    return extra if extra else None


def fetch_activities(
    headers: dict,
    start_date: date | None,
    end_date: date | None,
    sport_types: list[str],
    fetch_details: bool = False,
) -> list[dict]:
    req_h = build_req_headers(headers)
    session_id = str(uuid.uuid4())
    results = []

    # Strava only accepts a single sport_type value; filter multi-type client-side
    api_sport_type = sport_types[0] if len(sport_types) == 1 else ""

    for page in range(1, 101):  # max 100 pages (≈3 000 activities)
        params = {
            "keywords": "",
            "sport_type": api_sport_type,
            "tags": "",
            "commute": "",
            "private_activities": "",
            "trainer": "",
            "gear": "",
            "search_session_id": session_id,
            "new_activity_only": "false",
            "order": "",
            "page": str(page),
            "per_page": "30",
        }

        r = requests.get(
            "https://www.strava.com/athlete/training_activities",
            headers=req_h,
            params=params,
            timeout=30,
        )

        if r.status_code in (401, 403):
            raise ValueError(
                "Authentication failed — please copy a fresh cURL command from Strava."
            )
        r.raise_for_status()

        try:
            data = r.json()
        except Exception:
            if "login" in r.url or (
                "sign_in" in r.text.lower() and len(r.text) < 10_000
            ):
                raise ValueError(
                    "Session expired — please copy a fresh cURL command from Strava."
                )
            raise ValueError(
                f"Strava returned an unexpected response (status {r.status_code}). "
                "Try copying a fresh cURL command."
            )

        page_acts = (
            data
            if isinstance(data, list)
            else data.get("models", data.get("activities", data.get("data", [])))
        )

        if not page_acts:
            break

        stop = False
        for act in page_acts:
            act_date = parse_act_date(act)
            if act_date:
                # Activities arrive newest-first; stop once past start_date
                if start_date and act_date < start_date:
                    stop = True
                    break
                if end_date and act_date > end_date:
                    continue

            # Client-side multi-type filter (API only supports a single type)
            if len(sport_types) > 1:
                act_sport = _get(act, "sport_type", "sportType", "type", "activityType") or ""
                if act_sport not in sport_types:
                    continue

            if fetch_details and act.get("id"):
                detail = fetch_activity_detail(act["id"], headers)
                if detail:
                    act = {**act, **detail}
                time.sleep(0.3)  # be polite when hitting detail endpoints

            results.append(act)

        if stop or len(page_acts) < 30:
            break

        time.sleep(0.15)

    return results


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/fetch-activities", methods=["POST"])
def api_fetch():
    body = request.get_json(force=True) or {}
    curl_str = (body.get("curl") or "").strip()

    if not curl_str:
        return jsonify({"error": "cURL command is required"}), 400

    try:
        _, headers = parse_curl(curl_str)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    start_date = end_date = None
    try:
        if body.get("start_date"):
            start_date = datetime.strptime(body["start_date"], "%Y-%m-%d").date()
        if body.get("end_date"):
            end_date = datetime.strptime(body["end_date"], "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Invalid date format — use YYYY-MM-DD"}), 400

    sport_types = body.get("sport_types") or []
    fetch_details = bool(body.get("fetch_details", False))

    try:
        activities = fetch_activities(
            headers, start_date, end_date, sport_types, fetch_details
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 401
    except requests.RequestException as e:
        return jsonify({"error": f"Network error: {e}"}), 502

    if activities:
        print("\n=== RAW SAMPLE (first activity) ===")
        print(json.dumps(activities[0], indent=2, default=str))
        print("==================================\n")

    return jsonify(
        {
            "count": len(activities),
            "formatted": format_all(activities),
            "compact": format_all_compact(activities),
            "raw": activities,
            "raw_sample": activities[0] if activities else None,
        }
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
