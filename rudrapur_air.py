"""Rudrapur Air: fetch, archive and render a daily air bulletin for Rudrapur, Uttarakhand.

Run:  python rudrapur_air.py            (normal update: last 10 days + 3-day forecast)
      python rudrapur_air.py --backfill 2026-10-10

Keys come from environment variables OPENAQ_KEY and FIRMS_KEY, or from a keys.env file
next to this script or one folder up. Uses the Python standard library only.
"""
import csv, html, io, json, math, os, statistics as st, sys, time, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
IST = timezone(timedelta(hours=5, minutes=30))
RUD = (28.98, 79.40)                       # Rudrapur town centre
KSP = (29.2124, 78.9612)                   # Kashipur, Govt Girls Inter College (UKPCB CAAQMS)
KSP_LOCATION = 3409409                     # OpenAQ location id of the Kashipur station
KSP_SENSOR_FALLBACK = 12242992             # its PM2.5 sensor on 9 Nov 2026
FIRE_BOX = "72,26,82,33"                   # west, south, east, north
PUNJAB_HARYANA = (29.0, 32.6, 73.5, 77.6)  # lat min, lat max, lon min, lon max
SITE_URL = os.environ.get("SITE_URL", "").strip()

# ---------------------------------------------------------------- keys & http
def keys():
    k = {n: os.environ.get(n, "").strip() for n in ("OPENAQ_KEY", "FIRMS_KEY")}
    for p in (os.path.join(HERE, "keys.env"), os.path.join(HERE, "..", "keys.env")):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8-sig"):
                if "=" in line and not line.lstrip().startswith("#"):
                    a, b = line.split("=", 1)
                    if a.strip() in k and not k[a.strip()]:
                        k[a.strip()] = b.strip().strip("'\"")
    return k

def get(url, headers=None, raw=False, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "rudrapur-air/1.0", **(headers or {})})
            b = urllib.request.urlopen(req, timeout=120).read()
            return b.decode("utf-8") if raw else json.loads(b)
        except Exception as e:
            print(f"  retry {i+1}: {str(e)[:90]}", file=sys.stderr)
            time.sleep(3 + 4 * i)
    return None

# ---------------------------------------------------------------- AQI (India, CPCB)
CATS = [  # upper PM2.5 bound (24-h), index range, key, English, Hindi
    (30, (0, 50), "good", "Good", "अच्छी"),
    (60, (51, 100), "sat", "Satisfactory", "संतोषजनक"),
    (90, (101, 200), "mod", "Moderate", "मध्यम"),
    (120, (201, 300), "poor", "Poor", "खराब"),
    (250, (301, 400), "vpoor", "Very poor", "बहुत खराब"),
    (1e9, (401, 500), "severe", "Severe", "गंभीर"),
]
ADVICE = {
    "good": ("The air is clean. A good day to spend time outside.",
             "हवा साफ़ है। बाहर समय बिताने के लिए अच्छा दिन।"),
    "sat": ("Fine for most people. People with asthma or heart trouble should take it easy in the evening.",
            "ज़्यादातर लोगों के लिए ठीक। अस्थमा या दिल के मरीज़ शाम को सावधानी रखें।"),
    "mod": ("Cut down long outdoor exercise, especially in the evening. Children, older people and people with asthma should take care.",
            "शाम को लंबा व्यायाम कम करें। बच्चे, बुज़ुर्ग और अस्थमा के मरीज़ सावधान रहें।"),
    "poor": ("Exercise only in the cleanest hours. Keep windows shut in the evening and at night.",
             "व्यायाम सिर्फ़ सबसे साफ़ समय में करें। शाम और रात को खिड़कियाँ बंद रखें।"),
    "vpoor": ("Avoid outdoor exercise. Wear an N95 mask outside and run an air purifier indoors.",
              "बाहर व्यायाम न करें। बाहर N95 मास्क पहनें और घर में एयर प्यूरीफ़ायर चलाएँ।"),
    "severe": ("Stay indoors as much as you can. Wear an N95 mask outside and keep windows shut.",
               "जितना हो सके घर के अंदर रहें। बाहर N95 मास्क पहनें और खिड़कियाँ बंद रखें।"),
}

def cat(pm):
    lo_pm = 0
    for hi_pm, (ilo, ihi), key, en, hi in CATS:
        if pm <= hi_pm:
            top = min(hi_pm, 380)
            aqi = ilo + (min(pm, top) - lo_pm) / (top - lo_pm) * (ihi - ilo) if top > lo_pm else ilo
            return {"aqi": int(round(min(aqi, 500))), "key": key, "en": en, "hi": hi}
        lo_pm = hi_pm

# ---------------------------------------------------------------- data sources
def fetch_model(start, end):
    pts = [RUD, KSP]
    base = {"latitude": f"{RUD[0]},{KSP[0]}", "longitude": f"{RUD[1]},{KSP[1]}", "timezone": "Asia/Kolkata",
            "start_date": start.isoformat(), "end_date": end.isoformat()}
    aq = get("https://air-quality-api.open-meteo.com/v1/air-quality?" + urllib.parse.urlencode({**base, "hourly": "pm2_5,dust"}))
    wx = get("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(
        {**base, "hourly": "boundary_layer_height,wind_speed_10m,wind_direction_10m,precipitation,relative_humidity_2m", "wind_speed_unit": "ms"}))
    if not aq or not wx:
        raise SystemExit("Open-Meteo did not answer; nothing was changed.")
    def table(block):
        h = block["hourly"]; return {t[:13]: {k: h[k][i] for k in h if k != "time"} for i, t in enumerate(h["time"])}
    return {"rud": table(aq[0]), "ksp": table(aq[1]), "wx": table(wx[0])}

def kashipur_sensor(k):
    d = get(f"https://api.openaq.org/v3/locations/{KSP_LOCATION}", {"X-API-Key": k}) if k else None
    try:
        s = [x for x in d["results"][0]["sensors"] if x["parameter"]["name"] == "pm25"]
        return s[0]["id"] if s else KSP_SENSOR_FALLBACK
    except Exception:
        return KSP_SENSOR_FALLBACK

def fetch_ground(k, start):
    if not k:
        print("No OPENAQ_KEY: skipping the Kashipur monitor.", file=sys.stderr); return {}
    sid, out, page = kashipur_sensor(k), {}, 1
    t0 = (datetime.combine(start, datetime.min.time(), IST) - timedelta(hours=1)).astimezone(timezone.utc)
    while True:
        u = (f"https://api.openaq.org/v3/sensors/{sid}/hours?datetime_from={t0:%Y-%m-%dT%H:%M:%SZ}"
             f"&limit=1000&page={page}")
        d = get(u, {"X-API-Key": k})
        r = (d or {}).get("results", [])
        for h in r:
            v = h.get("value")
            if v is not None and 0 < v < 999:
                out[h["period"]["datetimeFrom"]["local"][:13]] = v
        if len(r) < 1000: break
        page += 1; time.sleep(0.4)
    return out

def fetch_fires(k, start, end):
    if not k:
        print("No FIRMS_KEY: skipping fires.", file=sys.stderr); return None
    fires, d, ok = [], start, False
    while d <= end:
        span = min(5, (end - d).days + 1)
        for sat in ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"):
            t = get(f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{k}/{sat}/{FIRE_BOX}/{span}/{d.isoformat()}", raw=True, tries=3)
            if not t or not t.startswith("latitude"): continue
            ok = True
            for r in csv.DictReader(io.StringIO(t)):
                if r.get("confidence", "n").strip().lower().startswith("l"): continue
                fires.append((r["acq_date"], float(r["latitude"]), float(r["longitude"]), float(r.get("frp") or 0)))
        d += timedelta(days=span)
    return fires if ok else None

# ---------------------------------------------------------------- helpers
def dist_km(a, b):
    p = math.pi / 180
    x = math.sin((b[0]-a[0])*p/2)**2 + math.cos(a[0]*p)*math.cos(b[0]*p)*math.sin((b[1]-a[1])*p/2)**2
    return 12742 * math.asin(math.sqrt(x))

def bearing(a, b):
    p = math.pi / 180
    y = math.sin((b[1]-a[1])*p) * math.cos(b[0]*p)
    x = math.cos(a[0]*p)*math.sin(b[0]*p) - math.sin(a[0]*p)*math.cos(b[0]*p)*math.cos((b[1]-a[1])*p)
    return (math.degrees(math.atan2(y, x)) + 360) % 360

def wind_from(rows):
    pairs = [(r["wind_speed_10m"], r["wind_direction_10m"]) for r in rows if r.get("wind_speed_10m") is not None]
    if not pairs: return None, None
    u = st.mean(-s*math.sin(math.radians(d)) for s, d in pairs); v = st.mean(-s*math.cos(math.radians(d)) for s, d in pairs)
    return round((math.degrees(math.atan2(-u, -v)) + 360) % 360), round(st.mean(s for s, _ in pairs), 1)

SECTORS = [("north", "उत्तर"), ("north-east", "उत्तर-पूर्व"), ("east", "पूर्व"), ("south-east", "दक्षिण-पूर्व"),
           ("south", "दक्षिण"), ("south-west", "दक्षिण-पश्चिम"), ("west", "पश्चिम"), ("north-west", "उत्तर-पश्चिम")]
def sector(deg): return SECTORS[int(((deg or 0) + 22.5) // 45) % 8]

def hours_of(day): return [f"{day.isoformat()}T{h:02d}" for h in range(24)]

def factor_for(day, ground, model_ksp):
    """Ratio of Kashipur monitor to model at Kashipur over the 7 days ending on `day`."""
    ts = [t for t in ground if t in model_ksp and model_ksp[t].get("pm2_5") is not None
          and (day - timedelta(days=6)).isoformat() <= t[:10] <= day.isoformat()]
    if len(ts) < 36: return None, len(ts)
    f = sum(ground[t] for t in ts) / max(1e-6, sum(model_ksp[t]["pm2_5"] for t in ts))
    return round(min(1.5, max(0.6, f)), 2), len(ts)

# ---------------------------------------------------------------- archive
DAILY_COLS = ["date", "final", "est_pm25", "aqi_in", "category", "model_pm25", "kashipur_pm25", "kashipur_hours",
              "factor", "factor_hours", "fires_25km", "fires_upwind_400km", "fires_punjab_haryana",
              "night_mixing_min_m", "wind_from_deg", "wind_ms", "rain_mm", "model_dust"]
HOURLY_COLS = ["time", "est_pm25", "model_pm25", "model_kashipur_pm25", "kashipur_pm25"]

def read_csv(p):
    return {r[next(iter(r))]: r for r in csv.DictReader(open(p, encoding="utf-8"))} if os.path.exists(p) else {}

def write_csv(p, cols, rows):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for k in sorted(rows): w.writerow({c: rows[k].get(c, "") for c in cols})

def num(x, d=1):
    return "" if x is None else (round(x, d) if d else int(round(x)))

# ---------------------------------------------------------------- main build
def build(backfill_from=None):
    K = keys()
    now = datetime.now(IST).replace(minute=0, second=0, microsecond=0)
    today = now.date()
    start = backfill_from or (today - timedelta(days=10))
    end = today + timedelta(days=3)
    print(f"Fetching {start} to {end} ...")
    M = fetch_model(start - timedelta(days=6), end)
    G = fetch_ground(K["OPENAQ_KEY"], start - timedelta(days=6))
    F = fetch_fires(K["FIRMS_KEY"], start - timedelta(days=1), today)
    print(f"  model hours {len(M['rud'])}, Kashipur hours {len(G)}, fires {'n/a' if F is None else len(F)}")

    daily_p = os.path.join(HERE, "data", "daily.csv")
    daily = read_csv(daily_p)
    cur_f, cur_n = factor_for(today, G, M["ksp"])
    if cur_f is None:  # not enough monitor data this week: reuse the last saved factor
        prev = [r for k, r in sorted(daily.items()) if r.get("factor")]
        cur_f = float(prev[-1]["factor"]) if prev else 1.0
    # today's own ratio at Kashipur, when the monitor has reported enough hours today
    tt = [t for t in G if t[:10] == today.isoformat() and M["ksp"].get(t, {}).get("pm2_5")]
    now_f = cur_f
    if len(tt) >= 6:
        rt = sum(G[t] for t in tt) / sum(M["ksp"][t]["pm2_5"] for t in tt)
        now_f = round(min(1.5, max(0.5, 0.5 * cur_f + 0.5 * rt)), 2)
    print(f"  7-day factor {cur_f} ({cur_n} h); today's blended factor {now_f} ({len(tt)} h today)")
    # past days: their own 7-day factor, or the nearest day that had enough monitor hours
    fac, d = {}, start
    while d < today:
        fac[d] = factor_for(d, G, M["ksp"]); d += timedelta(days=1)
    good = [x for x in fac if fac[x][0] is not None]
    for x in fac:
        if fac[x][0] is None and good:
            near = min(good, key=lambda g: abs((g - x).days)); fac[x] = (fac[near][0], 0)
    days, d = {}, start
    while d <= end:
        f, n = fac.get(d, (None, 0))
        if f is None:
            f = now_f if d == today else (round((now_f + cur_f) / 2, 2) if d == today + timedelta(days=1) else cur_f)
            n = cur_n if d >= today else 0
        hrs = [t for t in hours_of(d) if M["rud"].get(t, {}).get("pm2_5") is not None]
        if len(hrs) < 20: d += timedelta(days=1); continue
        model = st.mean(M["rud"][t]["pm2_5"] for t in hrs)
        dust = st.mean(M["rud"][t].get("dust") or 0 for t in hrs)
        gk = [G[t] for t in hours_of(d) if t in G]
        night = [M["wx"][t]["boundary_layer_height"] for t in
                 [f"{d.isoformat()}T{h:02d}" for h in range(18, 24)] + [f"{(d+timedelta(days=1)).isoformat()}T{h:02d}" for h in range(0, 7)]
                 if M["wx"].get(t, {}).get("boundary_layer_height") is not None]
        wdir, wspd = wind_from([M["wx"][t] for t in hours_of(d) if t in M["wx"]])
        rain = sum(M["wx"][t].get("precipitation") or 0 for t in hours_of(d) if t in M["wx"])
        row = {"date": d.isoformat(), "final": int(d < today), "est_pm25": num(model * f), "model_pm25": num(model),
               "kashipur_pm25": num(st.mean(gk)) if len(gk) >= 12 else "", "kashipur_hours": len(gk),
               "factor": f, "factor_hours": n, "night_mixing_min_m": num(min(night), 0) if night else "",
               "wind_from_deg": wdir if wdir is not None else "", "wind_ms": wspd if wspd is not None else "",
               "rain_mm": num(rain), "model_dust": num(dust)}
        c = cat(model * f); row["aqi_in"], row["category"] = c["aqi"], c["en"]
        if F is not None and d <= today:
            fd = [x for x in F if x[0] == d.isoformat()]
            row["fires_25km"] = sum(1 for x in fd if dist_km(RUD, x[1:3]) <= 25)
            row["fires_punjab_haryana"] = sum(1 for x in fd if PUNJAB_HARYANA[0] <= x[1] <= PUNJAB_HARYANA[1] and PUNJAB_HARYANA[2] <= x[2] <= PUNJAB_HARYANA[3])
            if wdir is not None:
                row["fires_upwind_400km"] = sum(1 for x in fd if dist_km(RUD, x[1:3]) <= 400 and abs((bearing(RUD, x[1:3]) - wdir + 180) % 360 - 180) <= 45)
        days[d.isoformat()] = row
        d += timedelta(days=1)

    # archive: keep everything older, replace the recomputed past days; future days are not archived
    for k, r in days.items():
        if k <= today.isoformat():
            old = daily.get(k, {})
            if F is None:  # keep earlier fire counts if FIRMS was unavailable this run
                for c in ("fires_25km", "fires_upwind_400km", "fires_punjab_haryana"):
                    r.setdefault(c, old.get(c, ""))
            daily[k] = r
    write_csv(daily_p, DAILY_COLS, daily)
    hourly = {}
    for t in sorted(M["rud"]):
        if t[:10] < start.isoformat() or t > now.strftime("%Y-%m-%dT%H"): continue
        dd = days.get(t[:10]); f = float(dd["factor"]) if dd else cur_f
        p = M["rud"][t].get("pm2_5")
        hourly.setdefault(t[:7], {})[t] = {"time": t, "est_pm25": num(p * f) if p is not None else "", "model_pm25": num(p),
                                           "model_kashipur_pm25": num(M["ksp"].get(t, {}).get("pm2_5")), "kashipur_pm25": num(G.get(t))}
    for ym, rows in hourly.items():
        p = os.path.join(HERE, "data", "hourly", f"{ym}.csv"); old = read_csv(p); old.update(rows); write_csv(p, HOURLY_COLS, old)

    # ------------------------------------------------ page model
    nxt = [now + timedelta(hours=i) for i in range(24)]
    strip = []
    for t in nxt:
        k = t.strftime("%Y-%m-%dT%H"); p = M["rud"].get(k, {}).get("pm2_5")
        if p is None: continue
        ff = now_f if t.date() == today else round((now_f + cur_f) / 2, 2)
        strip.append({"h": t.hour, "day": t.date().isoformat(), "pm": round(p * ff), "c": cat(p * ff)["key"]})
    def window(cands, best):
        sums = [(sum(x["pm"] for x in strip[i:i+3]) / 3, i) for i in range(len(strip) - 2) if all(cands(x) for x in strip[i:i+3])]
        return (min if best else max)(sums) if sums else None
    bw = window(lambda x: 7 <= x["h"] <= 18, True)
    ww = window(lambda x: True, False)
    tday = days.get(today.isoformat())
    nowp = strip[0]["pm"] if strip else None
    ksp_now = None
    if G:
        lt = max(G); age = (now - datetime.strptime(lt, "%Y-%m-%dT%H").replace(tzinfo=IST)).total_seconds() / 3600
        if age <= 8: ksp_now = {"time": lt[11:13] + ":00", "pm": round(G[lt]), "age": round(age)}
    fc = [days[(today + timedelta(days=i)).isoformat()] for i in (1, 2, 3) if (today + timedelta(days=i)).isoformat() in days]
    hist = [daily[k] for k in sorted(daily) if k <= today.isoformat()][-30:]

    # drivers for today (tonight's lid, today's wind, last-2-day fires)
    drv = []
    nm = tday and tday["night_mixing_min_m"]
    if nm != "" and nm is not None and float(nm) < 60:
        drv.append((f"A very low lid tonight: the air will mix only up to about {int(float(nm))} m, so smoke stays at breathing height.",
                    f"आज रात हवा का ढक्कन बहुत नीचे रहेगा: हवा सिर्फ़ लगभग {int(float(nm))} मीटर तक मिलेगी, इसलिए धुआँ साँस की ऊँचाई पर ही रहेगा।"))
    if tday and tday["wind_ms"] != "":
        s_en, s_hi = sector(tday["wind_from_deg"]); w = float(tday["wind_ms"])
        if w < 1.5:
            drv.append((f"Almost no wind ({w} m/s, from the {s_en}), so pollution builds up instead of blowing away.",
                        f"हवा लगभग रुकी हुई है ({w} मी/से, {s_hi} से), इसलिए प्रदूषण जमा होता जा रहा है।"))
        elif w >= 2.5 or float(tday["rain_mm"] or 0) >= 1:
            drv.append((f"Wind from the {s_en} at {w} m/s is helping to clear the air.",
                        f"{s_hi} से {w} मी/से की हवा प्रदूषण को साफ़ करने में मदद कर रही है।"))
        else:
            drv.append((f"Light wind from the {s_en} ({w} m/s).", f"{s_hi} से हल्की हवा ({w} मी/से)।"))
    y = days.get((today - timedelta(days=1)).isoformat(), {})
    def two(c):
        v = [r.get(c) for r in (tday or {}, y) if r and r.get(c) not in ("", None)]
        return sum(int(x) for x in v) if v else None
    up, loc = two("fires_upwind_400km"), two("fires_25km")
    if up and up >= 50:
        drv.append((f"{up} farm fires upwind of Rudrapur in the last two days, within 400 km.",
                    f"पिछले दो दिनों में हवा की दिशा में, 400 किमी के अंदर, {up} खेतों में आग।"))
    if loc:
        drv.append((f"{loc} fires within 25 km of Rudrapur in the last two days, mostly crop or waste burning.",
                    f"पिछले दो दिनों में रुद्रपुर के 25 किमी के अंदर {loc} जगह आग: ज़्यादातर पराली या कूड़ा जलाना।"))
    if tday and tday["model_dust"] != "" and float(tday["model_dust"]) > 20:
        drv.append(("The forecast includes a lot of dust. It often overstates dust here, so the real level may be lower.",
                    "पूर्वानुमान में धूल ज़्यादा दिख रही है। यहाँ यह अक्सर धूल को बढ़ा-चढ़ाकर दिखाता है, इसलिए असली स्तर कम हो सकता है।"))
    drv.append(("Every day: traffic, factories and brick kilns, plus damp air that turns farm fertiliser gases into fine particles.",
                "रोज़ाना: गाड़ियाँ, फ़ैक्टरियाँ और ईंट भट्ठे, और नम हवा जो खेतों की खाद से निकली गैसों को बारीक कणों में बदल देती है।"))

    data = {"updated": now.strftime("%d %b %Y, %H:%M IST"), "today": today.isoformat(), "factor": cur_f, "now_factor": now_f, "today_hours": len(tt),
            "factor_hours": cur_n, "now_pm": nowp, "today_row": tday, "strip": strip, "best": bw, "worst": ww,
            "kashipur_now": ksp_now, "forecast": fc, "history": hist, "drivers": drv}
    render(data)
    print(f"Done. Today {tday['est_pm25'] if tday else '?'} µg/m³ ({tday['category'] if tday else '?'}), factor {cur_f} from {cur_n} h.")

# ---------------------------------------------------------------- render
def esc(s): return html.escape(str(s))
def bi(en, hi): return f'<span class="en">{esc(en)}</span><span class="hi" lang="hi">{esc(hi)}</span>'
def ckey(row): return cat(float(row["est_pm25"]))["key"]
def dlabel(iso, short=False):
    d = date.fromisoformat(iso); m = d.strftime("%b")
    return f"{d.day} {m}" if short else d.strftime("%a ") + f"{d.day} {m}"
HI_DAYS = ["सोम", "मंगल", "बुध", "गुरु", "शुक्र", "शनि", "रवि"]
HI_MON = ["जन", "फ़र", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अग", "सित", "अक्टू", "नव", "दिस"]
def dlabel_hi(iso):
    d = date.fromisoformat(iso); return f"{HI_DAYS[d.weekday()]} {d.day} {HI_MON[d.month-1]}"
def hh(h): return f"{h:02d}:00"

def window_text(w, strip, today_iso):
    if not w: return None
    i = w[1]; a, b = strip[i]["h"], (strip[i+2]["h"] + 1) % 24
    t = f"{hh(a)}–{hh(b)}"
    if strip[i]["day"] != today_iso:
        if a < 6: return t, round(w[0]), f"tonight {t}", f"आज रात {t}"
        return t, round(w[0]), f"tomorrow {t}", f"कल {t}"
    return t, round(w[0]), f"today {t}", f"आज {t}" 

def chart_svg(hist):
    W, H, l, r, t, b = 680, 230, 34, 8, 12, 26
    vals = [float(x["est_pm25"]) for x in hist] + [float(x["kashipur_pm25"]) for x in hist if x["kashipur_pm25"] != ""]
    top = max(100, math.ceil(max(vals + [60]) / 20) * 20)
    n = len(hist); bw = (W - l - r) / max(n, 1)
    y = lambda v: t + (H - t - b) * (1 - v / top)
    out = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-labelledby="ch-t"><title id="ch-t">Daily PM2.5 in Rudrapur over the last {n} days</title>']
    for v in range(0, top + 1, 20 if top <= 140 else 40):
        out.append(f'<line class="grid" x1="{l}" x2="{W-r}" y1="{y(v):.1f}" y2="{y(v):.1f}"/><text class="tick" x="{l-6}" y="{y(v)+3.5:.1f}" text-anchor="end">{v}</text>')
    for i, x in enumerate(hist):
        v = float(x["est_pm25"]); c = ckey(x); x0 = l + i * bw + 1; w = max(2, bw - 2)
        h0 = max(1.5, y(0) - y(v))
        tip = f'{dlabel(x["date"])}: Rudrapur ~{v:.0f} µg/m³ ({x["category"]})' + (f', Kashipur monitor {float(x["kashipur_pm25"]):.0f}' if x["kashipur_pm25"] != "" else "")
        out.append(f'<path class="bar c-{c}" d="M{x0:.1f},{y(0):.1f} v{-(h0-3):.1f} q0,-3 3,-3 h{w-6:.1f} q3,0 3,3 v{h0-3:.1f} z"><title>{esc(tip)}</title></path>')
    for i, x in enumerate(hist):
        if x["kashipur_pm25"] != "":
            out.append(f'<circle class="dot" cx="{l + i*bw + bw/2:.1f}" cy="{y(float(x["kashipur_pm25"])):.1f}" r="4"><title>{esc(dlabel(x["date"]))}: Kashipur monitor {float(x["kashipur_pm25"]):.0f} µg/m³</title></circle>')
    for v, lab in ((60, "India limit 60"),):
        out.append(f'<line class="ref" x1="{l}" x2="{W-r}" y1="{y(v):.1f}" y2="{y(v):.1f}"/><text class="reflab" x="{W-r-4}" y="{y(v)-5:.1f}" text-anchor="end">{lab}</text>')
    for i, x in enumerate(hist):
        if i % 7 == 0 or i == n - 1:
            out.append(f'<text class="tick" x="{l + i*bw + bw/2:.1f}" y="{H-8}" text-anchor="middle">{esc(dlabel(x["date"], True))}</text>')
    out.append("</svg>")
    return "".join(out)

def render(D):
    tr = D["today_row"]; c = cat(float(tr["est_pm25"])) if tr else None
    strip = D["strip"]; best = window_text(D["best"], strip, D["today"]); worst = window_text(D["worst"], strip, D["today"])
    # 24-hour strip
    cells = []
    for i, x in enumerate(strip):
        lab = f'<span class="hr">{x["h"]:02d}</span>' if x["h"] % 3 == 0 else ""
        mark = ""
        if D["best"] and D["best"][1] <= i < D["best"][1] + 3: mark = " best"
        if D["worst"] and D["worst"][1] <= i < D["worst"][1] + 3: mark = " worst"
        cells.append(f'<div class="cell c-{x["c"]}{mark}" title="{hh(x["h"])}: ~{x["pm"]} µg/m³"><i></i>{lab}</div>')
    fc_cards = []
    for r in D["forecast"]:
        k = cat(float(r["est_pm25"]))
        fc_cards.append(f'''<li class="day"><span class="dname">{bi(dlabel(r["date"]), dlabel_hi(r["date"]))}</span>
          <span class="chip c-{k["key"]}">{bi(k["en"], k["hi"])}</span>
          <span class="dval"><b>{float(r["est_pm25"]):.0f}</b> µg/m³ · AQI {k["aqi"]}</span></li>''')
    drivers = "".join(f"<li>{bi(en, hi)}</li>" for en, hi in D["drivers"])
    kn = D["kashipur_now"]
    knote = (bi(f'Kashipur official monitor at {kn["time"]}: {kn["pm"]} µg/m³', f'काशीपुर सरकारी मॉनिटर, {kn["time"]} बजे: {kn["pm"]} µg/m³')
             if kn else bi("The Kashipur official monitor has not reported in the last 8 hours.", "काशीपुर का सरकारी मॉनिटर पिछले 8 घंटों से डेटा नहीं दे रहा।"))
    fc_short_en = ", ".join(f'{dlabel(r["date"]).split()[0]} {cat(float(r["est_pm25"]))["en"].lower()}' for r in D["forecast"])
    fc_short_hi = ", ".join(f'{dlabel_hi(r["date"]).split()[0]} {cat(float(r["est_pm25"]))["hi"]}' for r in D["forecast"])
    link = f" {SITE_URL}" if SITE_URL else ""
    wa_en = (f'Rudrapur air today: {c["en"]} (AQI {c["aqi"]}, PM2.5 about {float(tr["est_pm25"]):.0f} µg/m³).'
             + (f' Best time outside: {best[2]}.' if best else "") + (f' Worst: {worst[2]}, keep windows shut.' if worst else "")
             + (f' Next days: {fc_short_en}.' if fc_short_en else "") + link) if c else ""
    wa_hi = (f'आज रुद्रपुर की हवा: {c["hi"]} (AQI {c["aqi"]}, PM2.5 लगभग {float(tr["est_pm25"]):.0f} µg/m³)।'
             + (f' बाहर जाने का सबसे अच्छा समय: {best[3]}।' if best else "") + (f' सबसे खराब समय: {worst[3]}, खिड़कियाँ बंद रखें।' if worst else "")
             + (f' आगे: {fc_short_hi}।' if fc_short_hi else "") + link) if c else ""
    rows = "".join(f'<tr><td>{esc(dlabel(x["date"]))}</td><td class="r">{float(x["est_pm25"]):.0f}</td><td>{esc(x["category"])}</td>'
                   f'<td class="r">{esc(x["kashipur_pm25"] and str(round(float(x["kashipur_pm25"]))) or "–")}</td>'
                   f'<td class="r">{esc(x.get("fires_25km") or "–")}</td></tr>' for x in reversed(D["history"]))
    tpl = open(os.path.join(HERE, "template.html"), encoding="utf-8").read()
    rep = {
        "{{UPDATED}}": esc(D["updated"]),
        "{{CAT_KEY}}": c["key"] if c else "sat",
        "{{CAT}}": bi(c["en"], c["hi"]) if c else "",
        "{{AQI}}": str(c["aqi"]) if c else "–",
        "{{PM}}": f'{float(tr["est_pm25"]):.0f}' if tr else "–",
        "{{NOW_PM}}": str(D["now_pm"]) if D["now_pm"] is not None else "–",
        "{{ADVICE}}": bi(*ADVICE[c["key"]]) if c else "",
        "{{KASHIPUR}}": knote,
        "{{STRIP}}": "".join(cells),
        "{{BEST}}": (f'<b>{bi(best[2][0].upper()+best[2][1:], best[3])}</b> <span class="muted">~{best[1]} µg/m³</span>' if best else bi("No clean daytime window in the next 24 hours.", "अगले 24 घंटों में दिन का कोई साफ़ समय नहीं।")),
        "{{WORST}}": (f'<b>{bi(worst[2][0].upper()+worst[2][1:], worst[3])}</b> <span class="muted">~{worst[1]} µg/m³</span>' if worst else "–"),
        "{{FORECAST}}": "".join(fc_cards),
        "{{DRIVERS}}": drivers,
        "{{CHART}}": chart_svg(D["history"]) if D["history"] else "",
        "{{NDAYS}}": str(len(D["history"])),
        "{{ROWS}}": rows,
        "{{WA_EN}}": esc(wa_en), "{{WA_HI}}": esc(wa_hi),
        "{{FACTOR}}": f'{D["factor"]:.2f}', "{{NOW_FACTOR}}": f'{D["now_factor"]:.2f}', "{{TODAY_HOURS}}": str(D["today_hours"]), "{{FACTOR_HOURS}}": str(D["factor_hours"]),
        "{{OG_DESC}}": esc(wa_en[:190]),
    }
    page = tpl
    for k, v in rep.items(): page = page.replace(k, v)
    os.makedirs(os.path.join(HERE, "docs"), exist_ok=True)
    head = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            f'<meta property="og:title" content="Rudrapur Air">\n<meta property="og:description" content="{esc(wa_en[:190])}">\n'
            '</head>\n<body>\n')
    open(os.path.join(HERE, "docs", "index.html"), "w", encoding="utf-8").write(head + page + "\n</body>\n</html>\n")
    open(os.path.join(HERE, "preview.html"), "w", encoding="utf-8").write(page)
    src = os.path.join(HERE, "data", "daily.csv")
    if os.path.exists(src):
        open(os.path.join(HERE, "docs", "daily.csv"), "w", encoding="utf-8", newline="").write(open(src, encoding="utf-8").read())

if __name__ == "__main__":
    bf = None
    if "--backfill" in sys.argv:
        bf = date.fromisoformat(sys.argv[sys.argv.index("--backfill") + 1])
    build(bf)
