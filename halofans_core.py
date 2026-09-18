#!/usr/bin/env python3
"""
halofans_core — fungsi inti (reusable) untuk data event halofans/moflip.

Dipakai oleh CLI (scrape_halofans.py) maupun bot Telegram (telegram_bot.py).
Semua data diambil dari API publik moflip; tidak ada login/pembayaran di sini.

Endpoint:
  GET https://spl.moflip.com/events?limit&offset   -> listing (id publik)
  GET https://spl.moflip.com/events/{slug_or_id}    -> detail + tiket
  GET https://spl.moflip.com/events/{slug}/ticket-status
  GET https://spl.moflip.com/tickets/{id}           -> kuota per tiket
"""
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

SITE_BASE = "https://vesta.halofans.id"
SPL_BASE = "https://spl.moflip.com"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json",
    "Referer": SITE_BASE + "/",
    "Origin": SITE_BASE,
}

TEST_MARKERS = ("test", "load test", "do not buy", "internal", "[testing]",
                "buat test", "-copy-", "dummy", "sandbox")


def is_test_event(name):
    n = (name or "").lower()
    return any(m in n for m in TEST_MARKERS)


def event_category(summary):
    """Klasifikasi ringan event berdasar nama/slug/harga.

    Return salah satu: 'compliment', 'private', 'regular'.
    - compliment : semua tiket gratis, atau nama/slug mengandung 'compliment'/'-com'
    - private    : nama/slug mengandung 'private'/'privat' (Private Link)
    """
    name = (summary.get("name") or "").lower()
    slug = (summary.get("slug") or "").lower()
    text = name + " " + slug
    if summary.get("all_free") or "compliment" in text or "-com" in slug:
        return "compliment"
    if "private" in text or "privat" in text or "-private" in slug:
        return "private"
    return "regular"


def session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _get_json(sess, url, timeout=(6, 10)):
    """GET JSON. timeout = (connect, read) supaya request tak pernah menggantung."""
    try:
        r = sess.get(url, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return None


def event_page_url(slug):
    return f"{SITE_BASE}/event/v2/{slug}"


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def get_event(sess, slug_or_id):
    """Return the moflip_event dict for a slug or id, or None."""
    j = _get_json(sess, f"{SPL_BASE}/events/{slug_or_id}")
    return (j or {}).get("data", {}).get("moflip_event")


def get_ticket_quota(sess, ticket_id):
    """Return the ticket's allocated quota (quantity), or None."""
    j = _get_json(sess, f"{SPL_BASE}/tickets/{ticket_id}")
    t = (j or {}).get("data", {}).get("moflip_ticket")
    return (t or {}).get("quantity")


def list_events(sess, limit=20):
    """Return raw listing objects for every LISTED event (walks all pages)."""
    events, offset = [], 0
    while True:
        j = _get_json(sess, f"{SPL_BASE}/events?limit={limit}&offset={offset}")
        if not j:
            break
        data = j.get("data", [])
        events.extend(data)
        pag = j.get("meta", {}).get("pagination", {})
        total = pag.get("total", len(events))
        if len(events) >= total or not data:
            break
        offset += limit
    seen, out = set(), []
    for e in events:
        if e.get("id") not in seen:
            seen.add(e.get("id"))
            out.append(e)
    return out


def _fetch_one(sess, eid):
    return eid, get_event(sess, eid)


def fetch_many(sess, ids, workers=8):
    """Fetch many event ids in parallel -> {id: moflip_event}."""
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_fetch_one, sess, i) for i in ids]
        for f in as_completed(futs):
            eid, ev = f.result()
            if ev:
                out[eid] = ev
    return out


# --------------------------------------------------------------------------- #
# Parsing / summarizing
# --------------------------------------------------------------------------- #
def invitation_of(ev):
    """Return invitation/compliment code config for an event, or None."""
    meta = ev.get("meta", {})
    ci = meta.get("custom_invitation")
    if not isinstance(ci, dict) or not ci:
        return None
    return {
        "field": meta.get("invitation_code_identifier"),
        "match_type": ci.get("type"),
        "values": ci.get("value"),
    }


def tickets_of(ev):
    """Normalized ticket list, sorted by price then id."""
    rows = []
    for t in ev.get("moflip_tickets", []):
        rows.append({
            "id": t.get("id"),
            "name": t.get("name"),
            "price": t.get("price") or 0,
            "status": t.get("status", "N/A"),
            "sale_start": t.get("sale_start"),
            "sale_end": t.get("sale_end"),
        })
    rows.sort(key=lambda x: (x["price"], x["id"] or 0))
    return rows


def _parse_dt(s):
    """Parse an ISO datetime string (e.g. 2026-10-03T05:00:00Z) -> aware dt or None."""
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _now_utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def availability(ev):
    """Return availability flags for an event based on its dates & tickets.

    Keys:
      event_over     : True kalau event sudah berakhir (event_end < now)
      not_started    : True kalau event belum mulai (event_start > now)
      redeemable     : True kalau ada >=1 tiket ACTIVE yang sale_end belum lewat
      on_sale        : True kalau ada >=1 tiket yang sedang dalam masa jual
                       (sale_start <= now <= sale_end) apa pun statusnya
    """
    now = _now_utc()
    ev_start = _parse_dt(ev.get("event_start"))
    ev_end = _parse_dt(ev.get("event_end"))
    event_over = bool(ev_end and ev_end < now)
    not_started = bool(ev_start and ev_start > now)

    redeemable = False
    on_sale = False
    for t in ev.get("moflip_tickets", []):
        ss = _parse_dt(t.get("sale_start"))
        se = _parse_dt(t.get("sale_end"))
        sale_open = (ss is None or ss <= now) and (se is None or se >= now)
        if sale_open:
            on_sale = True
            if t.get("status") == "ACTIVE":
                redeemable = True
    return {
        "event_over": event_over,
        "not_started": not_started,
        "redeemable": redeemable,
        "on_sale": on_sale,
    }


def summarize(ev):
    """Compact summary of an event."""
    meta = ev.get("meta", {})
    tk = tickets_of(ev)
    all_free = bool(tk) and all(t["price"] == 0 for t in tk)
    inv = invitation_of(ev)
    avail = availability(ev)
    return {
        "id": ev.get("id"),
        "slug": ev.get("slug"),
        "name": ev.get("name"),
        "status": ev.get("status"),
        "date": meta.get("date"),
        "location": meta.get("location"),
        "is_test": is_test_event(ev.get("name")),
        "all_free": all_free,
        "codes": (inv or {}).get("values"),
        "tickets": tk,
        "event_start": ev.get("event_start"),
        "event_end": ev.get("event_end"),
        "event_over": avail["event_over"],
        "not_started": avail["not_started"],
        "redeemable": avail["redeemable"],
        "on_sale": avail["on_sale"],
        "url": event_page_url(ev.get("slug")) if ev.get("slug") else None,
    }


def main_cluster_range(listed_ids, pad=30, max_span=300):
    """Compute a sane id-range around the largest dense cluster of ids.

    Ignores outliers (e.g. LOAD TEST id 9999) so we never scan thousands of ids.
    """
    s = sorted(set(i for i in listed_ids if isinstance(i, int)))
    if not s:
        return None, None
    clusters, cur = [], [s[0]]
    for x in s[1:]:
        if x - cur[-1] <= 100:
            cur.append(x)
        else:
            clusters.append(cur)
            cur = [x]
    clusters.append(cur)
    main = max(clusters, key=len)
    lo = max(1, min(main) - pad)
    hi = max(main) + pad
    if hi - lo + 1 > max_span:
        lo = hi - max_span + 1
    return lo, hi


def collect_all(sess, scan_pad=30, workers=8, include_hidden=True, look_ahead=60,
                deep=False, deep_floor=2000, deep_ceiling_pad=60):
    """Return {id: summary} for all events (listing + hidden id-range scan).

    - scan_pad  : lebar scan di sekitar cluster event nyata (mode ringan).
    - look_ahead: SELALU scan sekian id DI ATAS id tertinggi (event nyata),
      supaya event hidden baru (yang id-nya melompat ke depan) tetap tertangkap.
    - deep=True : MODE MENYELURUH. Scan dari deep_floor sampai (id tertinggi
      + deep_ceiling_pad), menemukan SEMUA compliment/private tersembunyi.
      Lebih lambat, tapi tidak ada yang terlewat.
    """
    listing = list_events(sess)
    ids = set(e["id"] for e in listing if isinstance(e.get("id"), int))
    real = [i for i in ids if i < 900000]

    if deep:
        # rentang penuh: floor .. (tertinggi + pad)
        top = max(real) if real else deep_floor
        ids.update(range(deep_floor, top + deep_ceiling_pad + 1))
    elif include_hidden and ids:
        lo, hi = main_cluster_range(ids, pad=scan_pad)
        if lo is not None:
            ids.update(range(lo, hi + 1))
            if real and look_ahead:
                top = max(real)
                ids.update(range(top + 1, top + look_ahead + 1))

    events = fetch_many(sess, sorted(ids), workers=workers)
    return {eid: summarize(ev) for eid, ev in events.items() if ev.get("id")}
