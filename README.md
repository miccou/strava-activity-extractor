# Strava Activity Extractor

Extract your Strava activities and format them for use with LLMs (ChatGPT, Claude, etc.).

No Strava API keys required — it authenticates via your browser session, just like the Strava web app does.

---

## Setup

**Requirements:** Python 3.10+

```bash
# 1. Clone / download this repo, then:
cd strava-activity-extractor

# 2. Create a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the app
python app.py
```

Open **http://localhost:5000** in your browser.

---

## How to get your cURL command

1. Go to [strava.com/athlete/training](https://www.strava.com/athlete/training) while logged in.
2. Open DevTools (`F12` / `Cmd+Option+I`) and go to the **Network** tab.
3. Reload the page or scroll down so new activity requests are made.
4. Find a request to `training_activities` in the list.
5. Right-click it → **Copy** → **Copy as cURL**.
6. Paste the entire command into the app.

> **Note:** The cURL command contains your session cookies. It will expire when your Strava session expires (typically within a few hours/days). Never share it with anyone.

---

## Usage

1. Paste the cURL command.
2. Set a date range (default: last 30 days).
3. Optionally filter by activity type using the toggle buttons.
4. Enable **"Fetch full details"** if you want splits/laps per activity (makes one extra request per activity — slower).
5. Click **Extract Activities**.
6. Copy the formatted output and paste it into your LLM of choice.

---

## Output format

Each activity is formatted as plain text like:

```
### 1.
Activity : Morning Run
Type     : Run
Link     : https://www.strava.com/activities/1234567890
Date     : Mon 12 May 2025, 07:00 AM
Distance : 10.52 km
Moving   : 1:02:30
Elapsed  : 1:04:15
Avg Pace : 5:57 /km
Max Pace : 4:32 /km
Elev Gain: 125m
Avg HR   : 152 bpm  ← requires "Fetch full details"
Max HR   : 178 bpm  ← requires "Fetch full details"
Avg Power: 228 W    ← requires "Fetch full details" (if power meter)
Effort   : 156      ← only shown when Strava has computed it (may be absent)
```

> **Note:** Calories are not available via Strava's web session endpoints and will not appear in the output.

If "Fetch full details" is enabled and the activity has splits:

```
Splits (10):
   1.  1.00 km  6:12  @ 6:12 /km  HR 145 bpm
   2.  1.00 km  5:58  @ 5:58 /km  HR 150 bpm
```

---

## Privacy

- The app runs entirely on your local machine.
- Your session cookies are sent **only** to `strava.com` — they pass through the local Flask server only to make the outbound request.
- Nothing is stored on disk.
