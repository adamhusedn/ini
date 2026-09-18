#!/usr/bin/env python3
"""
Bot Telegram asisten tiket halofans/moflip (read-only + notifikasi).

Fitur:
  /start, /help          - bantuan
  /list                  - daftar semua event (termasuk hidden), tombol buka
  /cek <slug>            - detail tiket + harga + status + tombol beli
  /kuota <slug>          - kuota total tiap tiket + status
  /compliment            - daftar event compliment/gratis + code undangan
  /cari <kata>           - cari event berdasar nama/slug
  /watch on|off|status   - notifikasi otomatis (event/code/SOLD_OUT baru)

Bot ini HANYA membaca data publik + mengirim link. TIDAK melakukan
login, pembelian, pembayaran, atau pengisian data apa pun — langkah beli
tetap dilakukan sendiri lewat tombol yang membuka halaman resmi.

Setup:
  pip install "python-telegram-bot>=20"
  set TELEGRAM_BOT_TOKEN=...        (Windows: setx TELEGRAM_BOT_TOKEN "...")
  python telegram_bot.py
"""
import asyncio
import html
import json
import os
import sys
from pathlib import Path

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.constants import ParseMode
    from telegram.ext import (Application, CommandHandler, ContextTypes)
except ImportError:
    sys.exit('Butuh python-telegram-bot. Jalankan:\n'
             '  pip install "python-telegram-bot>=20"')

import halofans_core as c

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
STATE_PATH = Path(__file__).parent / "output" / "bot_watch_state.json"
# chat ids yang mengaktifkan /watch (disimpan agar persist antar-restart)
SUBS_PATH = Path(__file__).parent / "output" / "bot_subscribers.json"
WATCH_INTERVAL = 900  # detik (15 menit)

# Deep scan menyeluruh: cek SEMUA id dari DEEP_FLOOR ke atas supaya tidak ada
# compliment/private link tersembunyi yang terlewat (lebih lambat, tapi lengkap).
DEEP_FLOOR = int(os.environ.get("HALOFANS_DEEP_FLOOR", "2000"))
SCAN_WORKERS = int(os.environ.get("HALOFANS_WORKERS", "25"))

_sess = c.session()


def scan_all():
    """Ambil semua event dengan deep scan menyeluruh (dipakai semua perintah).
    Pakai session terpisah agar aman dipanggil dari beberapa thread."""
    return c.collect_all(c.session(), deep=True, deep_floor=DEEP_FLOOR, workers=SCAN_WORKERS)


async def scan_all_async(timeout=240):
    """Jalankan deep scan di thread terpisah supaya bot tidak freeze.
    Return {} kalau melebihi timeout."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(scan_all), timeout=timeout)
    except asyncio.TimeoutError:
        return {}
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def esc(s):
    return html.escape(str(s if s is not None else ""))


def rupiah(v):
    return "GRATIS" if not v else f"Rp{v:,}".replace(",", ".")


def status_emoji(status):
    return {"ACTIVE": "🟢", "SOLD_OUT": "🔴"}.get(status, "⚪")


def _load(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return default


def _save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def buy_button(slug, label="🎟️ Buka halaman beli"):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, url=c.event_page_url(slug))]])


TG_LIMIT = 3800  # aman di bawah batas Telegram 4096 char


def _chunk_lines(lines, limit=TG_LIMIT):
    """Gabung list baris menjadi beberapa pesan, tiap pesan < limit char."""
    chunks, buf = [], ""
    for ln in lines:
        add = (ln + "\n")
        if len(buf) + len(add) > limit and buf:
            chunks.append(buf.rstrip())
            buf = ""
        buf += add
    if buf.strip():
        chunks.append(buf.rstrip())
    return chunks or [""]


async def send_long(update_or_msg, header_lines, item_lines, edit_first=None):
    """Kirim header + banyak item, otomatis dipecah bila melebihi batas Telegram.

    - edit_first: message object untuk di-edit sebagai pesan pertama (opsional).
    """
    all_chunks = _chunk_lines(header_lines + item_lines)
    for i, chunk in enumerate(all_chunks):
        if i == 0 and edit_first is not None:
            await edit_first.edit_text(chunk, parse_mode=ParseMode.HTML,
                                       disable_web_page_preview=True)
        else:
            await update_or_msg.reply_text(chunk, parse_mode=ParseMode.HTML,
                                           disable_web_page_preview=True)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>🎫 Bot Tiket Halofans</b>\n\n"
        "Perintah:\n"
        "• <code>/aktif</code> — ⭐ semua yang MASIH BERLAKU (compliment + "
        "private + tiket dijual), tanpa event lewat\n"
        "• <code>/list</code> — semua event (termasuk hidden)\n"
        "• <code>/cek &lt;slug&gt;</code> — detail tiket + tombol beli\n"
        "• <code>/kuota &lt;slug&gt;</code> — kuota tiap tiket\n"
        "• <code>/compliment</code> — event gratis MASIH BERLAKU + code\n"
        "   (pakai <code>/compliment semua</code> untuk lihat semuanya)\n"
        "• <code>/cari &lt;kata&gt;</code> — cari event\n"
        "• <code>/watch compliment</code> — notif hanya compliment/gratis 🆓\n"
        "• <code>/watch private</code> — notif hanya private link 🔒\n"
        "• <code>/watch all</code> — notif semua event\n"
        "• <code>/watch off</code> — matikan notif\n\n"
        "Contoh: <code>/cek indo-comic</code>\n\n"
        "<i>Bot ini hanya menampilkan info & link resmi. Pembelian dilakukan "
        "sendiri lewat tombol.</i>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)


async def cmd_cek(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Format: /cek <slug>\nContoh: /cek indo-comic")
        return
    slug = ctx.args[0].strip()
    msg = await update.message.reply_text("⏳ mengambil data ...")
    ev = c.get_event(_sess, slug)
    if not ev:
        await msg.edit_text(f"❌ Event '{esc(slug)}' tidak ditemukan.")
        return
    s = c.summarize(ev)
    lines = [f"<b>{esc(s['name'])}</b>"]
    if s["date"]:
        lines.append(f"📅 {esc(s['date'])}")
    if s["location"]:
        lines.append(f"📍 {esc(s['location'])}")
    # status keberlakuan
    if s["event_over"]:
        lines.append("⛔ <b>Event sudah berakhir</b>")
    elif s["not_started"]:
        lines.append("⏳ Event belum mulai" + (" · tiket bisa di-redeem" if s["redeemable"] else ""))
    elif s["redeemable"]:
        lines.append("✅ Masih berlaku (tiket bisa dibeli/redeem)")
    if s["codes"]:
        codes = ", ".join(f"<code>{esc(x)}</code>" for x in s["codes"])
        lines.append(f"🎟️ <b>Code undangan:</b> {codes}")
    lines.append("")
    lines.append("<b>Tiket:</b>")
    for t in s["tickets"]:
        lines.append(f"{status_emoji(t['status'])} {esc(t['name'])} — "
                     f"<b>{rupiah(t['price'])}</b> [{esc(t['status'])}]")
    await msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML,
                        reply_markup=buy_button(s["slug"]),
                        disable_web_page_preview=True)


async def cmd_kuota(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Format: /kuota <slug>\nContoh: /kuota indo-comic")
        return
    slug = ctx.args[0].strip()
    msg = await update.message.reply_text("⏳ mengambil kuota ...")
    ev = c.get_event(_sess, slug)
    if not ev:
        await msg.edit_text(f"❌ Event '{esc(slug)}' tidak ditemukan.")
        return
    s = c.summarize(ev)
    lines = [f"<b>{esc(s['name'])}</b>", "<b>Kuota tiket:</b>"]
    total = 0
    for t in s["tickets"]:
        q = c.get_ticket_quota(_sess, t["id"])
        total += q or 0
        lines.append(f"{status_emoji(t['status'])} {esc(t['name'])} — "
                     f"{rupiah(t['price'])} · kuota <b>{q if q is not None else '?'}</b>")
    lines.append(f"\nTotal kuota: <b>{total:,}</b>".replace(",", "."))
    lines.append("<i>Catatan: ini kuota total alokasi, bukan sisa real-time.</i>")
    await msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML,
                        reply_markup=buy_button(s["slug"]),
                        disable_web_page_preview=True)


def _event_line(s):
    free = " 🆓" if s["all_free"] else ""
    return (f"• <a href=\"{c.event_page_url(s['slug'])}\">{esc(s['name'])[:55]}</a>{free}\n"
            f"   <code>/cek {esc(s['slug'])}</code>")


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ mengumpulkan semua event (termasuk hidden) ...")
    allev = await scan_all_async()
    if not allev:
        await msg.edit_text('⚠️ Scan gagal/timeout. Server halofans mungkin lambat. Coba lagi sebentar.')
        return
    real = [s for s in allev.values() if not s["is_test"]]
    real.sort(key=lambda x: x["id"], reverse=True)
    header = [f"<b>📋 {len(real)} event</b> (terbaru di atas):\n"]
    items = [_event_line(s) for s in real]
    await send_long(update.message, header, items, edit_first=msg)


async def cmd_compliment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # /compliment       -> hanya yang MASIH BERLAKU (default)
    # /compliment semua -> semua termasuk yang sudah lewat/habis
    show_all = bool(ctx.args) and ctx.args[0].lower() in ("semua", "all", "-a")
    msg = await update.message.reply_text("⏳ mencari event compliment/gratis ...")
    allev = await scan_all_async()
    if not allev:
        await msg.edit_text('⚠️ Scan gagal/timeout. Server halofans mungkin lambat. Coba lagi sebentar.')
        return
    comp = [s for s in allev.values()
            if (s["all_free"] or s["codes"]) and not s["is_test"]]

    if not show_all:
        comp = [s for s in comp if not s["event_over"] and s["redeemable"]]

    comp.sort(key=lambda x: x["id"], reverse=True)
    if not comp:
        extra = "" if show_all else " yang masih berlaku"
        await msg.edit_text(f"Tidak ada event compliment/gratis{extra} saat ini.\n"
                            "Coba <code>/compliment semua</code> untuk melihat semuanya.",
                            parse_mode=ParseMode.HTML)
        return

    judul = "semua compliment/gratis" if show_all else "compliment/gratis MASIH BERLAKU"
    header = [f"<b>🆓 {len(comp)} event {judul}:</b>"]
    if not show_all:
        header.append("<i>(event belum lewat & tiket bisa di-redeem)</i>")
    header.append("")
    items = []
    for s in comp:
        tag = "🆓GRATIS" if s["all_free"] else "🎟️code"
        when = ""
        if s["not_started"]:
            when = " ⏳belum mulai"
        line = f"• <a href=\"{c.event_page_url(s['slug'])}\">{esc(s['name'])[:50]}</a> [{tag}]{when}"
        if s["date"]:
            line += f"\n   📅 {esc(s['date'])}"
        if s["codes"]:
            line += "\n   code: " + ", ".join(f"<code>{esc(x)}</code>" for x in s["codes"])
        line += f"\n   <code>/cek {esc(s['slug'])}</code>"
        items.append(line)
    await send_long(update.message, header, items, edit_first=msg)


async def cmd_cari(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Format: /cari <kata>\nContoh: /cari persija")
        return
    q = " ".join(ctx.args).lower()
    msg = await update.message.reply_text(f"⏳ mencari '{esc(q)}' ...")
    allev = await scan_all_async()
    if not allev:
        await msg.edit_text('⚠️ Scan gagal/timeout. Server halofans mungkin lambat. Coba lagi sebentar.')
        return
    hits = [s for s in allev.values()
            if q in (s["name"] or "").lower() or q in (s["slug"] or "").lower()]
    hits.sort(key=lambda x: x["id"], reverse=True)
    if not hits:
        await msg.edit_text(f"Tidak ada event cocok dengan '{esc(q)}'.")
        return
    header = [f"<b>🔎 {len(hits)} hasil untuk '{esc(q)}':</b>\n"]
    items = [_event_line(s) for s in hits]
    await send_long(update.message, header, items, edit_first=msg)


def _is_active(s):
    """Event masih relevan: belum berakhir DAN ada tiket yang bisa dibeli/redeem."""
    return (not s["event_over"]) and (s["redeemable"] or s["on_sale"])


def _active_line(s, show_code=True):
    when = " ⏳belum mulai" if s["not_started"] else ""
    line = f"• <a href=\"{c.event_page_url(s['slug'])}\">{esc(s['name'])[:55]}</a>{when}"
    if s["date"]:
        line += f"\n   📅 {esc(s['date'])}"
    if show_code and s["codes"]:
        line += "\n   🎟️ code: " + ", ".join(f"<code>{esc(x)}</code>" for x in s["codes"])
    line += f"\n   <code>/cek {esc(s['slug'])}</code>"
    return line


async def cmd_aktif(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ringkasan SEMUA yang masih berlaku: compliment, private link, tiket dijual.

    Hasil deep scan; event yang sudah lewat / tiket mati otomatis dibuang.
    """
    msg = await update.message.reply_text(
        "⏳ deep scan semua event (compliment, private, tiket aktif) ...\n"
        "Ini butuh ~1-2 menit, mohon tunggu.")
    allev = await scan_all_async()
    if not allev:
        await msg.edit_text('⚠️ Scan gagal/timeout. Server halofans mungkin lambat. Coba lagi sebentar.')
        return
    allev = {k: v for k, v in allev.items() if not v["is_test"]}

    active = [v for v in allev.values() if _is_active(v)]
    comp = [v for v in active if c.event_category(v) == "compliment"]
    priv = [v for v in active if c.event_category(v) == "private"]
    reg = [v for v in active if c.event_category(v) == "regular"]
    for arr in (comp, priv, reg):
        arr.sort(key=lambda x: x["id"], reverse=True)

    header = [
        "<b>✅ Event yang MASIH BERLAKU</b>",
        "<i>(deep scan · event lewat & tiket mati sudah dibuang)</i>",
        f"🆓 compliment: {len(comp)}  ·  🔒 private: {len(priv)}  ·  🎟️ dijual: {len(reg)}",
        "",
    ]
    items = []
    if comp:
        items.append("<b>🆓 COMPLIMENT / GRATIS</b>")
        items += [_active_line(s) for s in comp]
        items.append("")
    if priv:
        items.append("<b>🔒 PRIVATE LINK</b>")
        items += [_active_line(s) for s in priv]
        items.append("")
    if reg:
        items.append("<b>🎟️ TIKET DIJUAL (berbayar)</b>")
        items += [_active_line(s, show_code=False) for s in reg]

    await send_long(update.message, header, items, edit_first=msg)


# --------------------------------------------------------------------------- #
# Watch / notifications
# --------------------------------------------------------------------------- #
WATCH_MODES = {
    "all": "semua event",
    "compliment": "hanya compliment/gratis",
    "private": "hanya private link",
}


def _load_subs():
    """Subscribers: {str(chat_id): mode}. Migrasi otomatis dari format list lama."""
    raw = _load(SUBS_PATH, {})
    if isinstance(raw, list):  # format lama (list chat_id) -> mode 'all'
        raw = {str(c): "all" for c in raw}
    return {str(k): v for k, v in raw.items()}


async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    arg = (ctx.args[0].lower() if ctx.args else "status")
    subs = _load_subs()
    chat_id = str(update.effective_chat.id)

    if arg == "off":
        subs.pop(chat_id, None)
        _save(SUBS_PATH, subs)
        await update.message.reply_text("🔕 Notifikasi OFF.")
        return

    if arg in ("on", "all", "compliment", "private"):
        mode = "all" if arg == "on" else arg
        subs[chat_id] = mode
        _save(SUBS_PATH, subs)
        await update.message.reply_text(
            f"✅ Notifikasi ON — <b>{WATCH_MODES[mode]}</b>.\n\n"
            "Kamu diberi tahu saat ada event/code baru atau tiket berubah "
            "(ACTIVE/SOLD_OUT) sesuai filter ini.\n\n"
            "Ganti filter: <code>/watch compliment</code>, "
            "<code>/watch private</code>, atau <code>/watch all</code>.",
            parse_mode=ParseMode.HTML)
        return

    # status
    if chat_id in subs:
        await update.message.reply_text(
            f"Status: ON ✅ — filter <b>{WATCH_MODES.get(subs[chat_id], subs[chat_id])}</b>\n\n"
            "Pilihan:\n"
            "• <code>/watch all</code> — semua event\n"
            "• <code>/watch compliment</code> — hanya compliment/gratis\n"
            "• <code>/watch private</code> — hanya private link\n"
            "• <code>/watch off</code> — matikan",
            parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(
            "Status: OFF 🔕\n\n"
            "Nyalakan dengan salah satu:\n"
            "• <code>/watch all</code> — semua event\n"
            "• <code>/watch compliment</code> — hanya compliment/gratis 🆓\n"
            "• <code>/watch private</code> — hanya private link 🔒",
            parse_mode=ParseMode.HTML)


async def cmd_debug(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Diagnosa: status watch, ukuran baseline, jumlah event ter-scan."""
    msg = await update.message.reply_text("⏳ diagnosa ...")
    subs = _load_subs()
    chat_id = str(update.effective_chat.id)
    allev = await scan_all_async()
    if not allev:
        await msg.edit_text('⚠️ Scan gagal/timeout. Server halofans mungkin lambat. Coba lagi sebentar.')
        return
    allev = {k: v for k, v in allev.items() if not v["is_test"]}
    ids = sorted(allev.keys())
    baseline = _load(STATE_PATH, {})
    from collections import Counter
    cats = Counter(v.get("category", c.event_category(v)) if isinstance(v, dict) else "" for v in allev.values())
    lines = [
        "<b>🔧 Debug watch</b>",
        f"• Notif kamu: {'ON ('+subs[chat_id]+')' if chat_id in subs else 'OFF'}",
        f"• Total subscriber: {len(subs)}",
        f"• Event ter-scan sekarang: <b>{len(ids)}</b> (id {min(ids)}..{max(ids)})" if ids else "• Event ter-scan: 0",
        f"• Kategori: compliment={cats.get('compliment',0)}, private={cats.get('private',0)}, regular={cats.get('regular',0)}",
        f"• Ukuran baseline tersimpan: {len(baseline)}",
        f"• Interval cek: tiap {WATCH_INTERVAL//60} menit",
        "",
        "Jika baseline=0 → belum pernah jalan; notif muncul mulai cek berikutnya.",
        "Jika event ter-scan &gt; baseline → ada event yang belum tercatat "
        "(akan dinotif di cek berikutnya kalau cocok filter).",
    ]
    await msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_testnotif(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Paksa jalankan siklus watch sekarang (untuk tes notif)."""
    await update.message.reply_text("⏳ menjalankan cek watch sekarang ...")
    await watch_job(ctx)
    await update.message.reply_text("✅ Selesai. Jika ada perubahan yang cocok "
                                    "filter kamu, notifnya sudah dikirim di atas.")


def _snapshot(allev):
    """Kompak: {id: {slug,name,all_free,codes,category, ticket_status}}."""
    snap = {}
    for eid, s in allev.items():
        snap[str(eid)] = {
            "slug": s["slug"], "name": s["name"], "all_free": s["all_free"],
            "codes": s["codes"], "category": c.event_category(s),
            "ts": {str(t["id"]): t["status"] for t in s["tickets"]},
        }
    return snap


def _mode_matches(mode, category):
    """Apakah event dengan kategori tsb cocok dengan mode subscriber."""
    if mode == "all":
        return True
    return mode == category


async def watch_job(ctx: ContextTypes.DEFAULT_TYPE):
    subs = _load_subs()
    if not subs:
        return
    allev = await scan_all_async()
    if not allev:
        return  # scan gagal/timeout; coba lagi di siklus berikutnya
    allev = {k: v for k, v in allev.items() if not v["is_test"]}
    new_snap = _snapshot(allev)
    old_snap = _load(STATE_PATH, {})
    _save(STATE_PATH, new_snap)
    if not old_snap:
        return  # baseline pertama

    # Kumpulkan perubahan sebagai (category, message-line)
    changes = []
    for eid, cur in new_snap.items():
        prev = old_snap.get(eid)
        cat = cur.get("category", "regular")
        url = c.event_page_url(cur["slug"])
        link = f"<a href=\"{url}\">{esc(cur['name'])[:55]}</a>"
        if prev is None:
            free = " 🆓" if cur["all_free"] else ""
            m = f"🆕 Event baru: {link}{free}"
            if cur["codes"]:
                m += "\n   code: " + ", ".join(f"<code>{esc(x)}</code>" for x in cur["codes"])
            changes.append((cat, m))
            continue
        if cur["codes"] and cur["codes"] != prev.get("codes"):
            changes.append((cat, f"🎟️ Code baru di {link}: " +
                            ", ".join(f"<code>{esc(x)}</code>" for x in cur["codes"])))
        for tid, st in cur["ts"].items():
            old_st = prev.get("ts", {}).get(tid)
            if old_st and old_st != st:
                if st == "ACTIVE":
                    changes.append((cat, f"🟢 Tiket DIBUKA di {link}"))
                elif st == "SOLD_OUT":
                    changes.append((cat, f"🔴 Tiket HABIS di {link}"))

    if not changes:
        return

    # Kirim ke tiap subscriber, disaring sesuai mode-nya
    for chat_id, mode in subs.items():
        msgs = [m for cat, m in changes if _mode_matches(mode, cat)]
        if not msgs:
            continue
        tag = {"all": "", "compliment": " (compliment)", "private": " (private link)"}.get(mode, "")
        chunks = _chunk_lines([f"<b>🔔 Update Halofans{tag}</b>", ""] + msgs)
        for chunk in chunks:
            try:
                await ctx.bot.send_message(int(chat_id), chunk, parse_mode=ParseMode.HTML,
                                           disable_web_page_preview=True)
            except Exception:
                pass


# --------------------------------------------------------------------------- #
def main():
    if not TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN belum di-set.\n"
                 "Windows:  setx TELEGRAM_BOT_TOKEN \"token-kamu\"  (lalu buka cmd baru)")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("cek", cmd_cek))
    app.add_handler(CommandHandler("kuota", cmd_kuota))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("compliment", cmd_compliment))
    app.add_handler(CommandHandler("cari", cmd_cari))
    app.add_handler(CommandHandler("aktif", cmd_aktif))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("debug", cmd_debug))
    app.add_handler(CommandHandler("testnotif", cmd_testnotif))
    # background job untuk notifikasi
    if app.job_queue:
        app.job_queue.run_repeating(watch_job, interval=WATCH_INTERVAL, first=30)
    print("Bot jalan. Tekan Ctrl+C untuk berhenti.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
