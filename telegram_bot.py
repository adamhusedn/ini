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

_sess = c.session()


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
        "• <code>/list</code> — semua event (termasuk hidden)\n"
        "• <code>/cek &lt;slug&gt;</code> — detail tiket + tombol beli\n"
        "• <code>/kuota &lt;slug&gt;</code> — kuota tiap tiket\n"
        "• <code>/compliment</code> — event gratis MASIH BERLAKU + code\n"
        "   (pakai <code>/compliment semua</code> untuk lihat semuanya)\n"
        "• <code>/cari &lt;kata&gt;</code> — cari event\n"
        "• <code>/watch on</code> — notif otomatis event/code/sold-out baru\n\n"
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
    allev = c.collect_all(_sess, scan_pad=30)
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
    allev = c.collect_all(_sess, scan_pad=30)
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
    allev = c.collect_all(_sess, scan_pad=30)
    hits = [s for s in allev.values()
            if q in (s["name"] or "").lower() or q in (s["slug"] or "").lower()]
    hits.sort(key=lambda x: x["id"], reverse=True)
    if not hits:
        await msg.edit_text(f"Tidak ada event cocok dengan '{esc(q)}'.")
        return
    header = [f"<b>🔎 {len(hits)} hasil untuk '{esc(q)}':</b>\n"]
    items = [_event_line(s) for s in hits]
    await send_long(update.message, header, items, edit_first=msg)


# --------------------------------------------------------------------------- #
# Watch / notifications
# --------------------------------------------------------------------------- #
async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    arg = (ctx.args[0].lower() if ctx.args else "status")
    subs = set(_load(SUBS_PATH, []))
    chat_id = update.effective_chat.id
    if arg == "on":
        subs.add(chat_id)
        _save(SUBS_PATH, list(subs))
        await update.message.reply_text(
            "✅ Notifikasi ON. Kamu akan diberi tahu saat ada event baru, "
            "code compliment baru, atau tiket berubah (ACTIVE/SOLD_OUT).")
    elif arg == "off":
        subs.discard(chat_id)
        _save(SUBS_PATH, list(subs))
        await update.message.reply_text("🔕 Notifikasi OFF.")
    else:
        state = "ON ✅" if chat_id in subs else "OFF 🔕"
        await update.message.reply_text(f"Status notifikasi kamu: {state}\n"
                                        "Ubah dengan /watch on atau /watch off")


def _snapshot(allev):
    """Kompak: {id: {slug,name,all_free,codes, ticket_status:{tid:status}}}."""
    snap = {}
    for eid, s in allev.items():
        snap[str(eid)] = {
            "slug": s["slug"], "name": s["name"], "all_free": s["all_free"],
            "codes": s["codes"],
            "ts": {str(t["id"]): t["status"] for t in s["tickets"]},
        }
    return snap


async def watch_job(ctx: ContextTypes.DEFAULT_TYPE):
    subs = _load(SUBS_PATH, [])
    if not subs:
        return
    allev = c.collect_all(_sess, scan_pad=30)
    allev = {k: v for k, v in allev.items() if not v["is_test"]}
    new_snap = _snapshot(allev)
    old_snap = _load(STATE_PATH, {})
    _save(STATE_PATH, new_snap)
    if not old_snap:
        return  # baseline pertama

    events_msgs = []
    for eid, cur in new_snap.items():
        prev = old_snap.get(eid)
        url = c.event_page_url(cur["slug"])
        link = f"<a href=\"{url}\">{esc(cur['name'])[:55]}</a>"
        if prev is None:
            free = " 🆓" if cur["all_free"] else ""
            m = f"🆕 Event baru: {link}{free}"
            if cur["codes"]:
                m += "\n   code: " + ", ".join(f"<code>{esc(x)}</code>" for x in cur["codes"])
            events_msgs.append(m)
            continue
        if cur["codes"] and cur["codes"] != prev.get("codes"):
            events_msgs.append(f"🎟️ Code baru di {link}: " +
                               ", ".join(f"<code>{esc(x)}</code>" for x in cur["codes"]))
        # perubahan status tiket
        for tid, st in cur["ts"].items():
            old_st = prev.get("ts", {}).get(tid)
            if old_st and old_st != st:
                if st == "ACTIVE":
                    events_msgs.append(f"🟢 Tiket DIBUKA di {link}")
                elif st == "SOLD_OUT":
                    events_msgs.append(f"🔴 Tiket HABIS di {link}")

    if not events_msgs:
        return
    chunks = _chunk_lines(["<b>🔔 Update Halofans</b>", ""] + events_msgs)
    for chat_id in subs:
        for chunk in chunks:
            try:
                await ctx.bot.send_message(chat_id, chunk, parse_mode=ParseMode.HTML,
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
    app.add_handler(CommandHandler("watch", cmd_watch))
    # background job untuk notifikasi
    if app.job_queue:
        app.job_queue.run_repeating(watch_job, interval=WATCH_INTERVAL, first=30)
    print("Bot jalan. Tekan Ctrl+C untuk berhenti.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
