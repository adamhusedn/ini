# Halofans / Moflip Event Scraper

Scraper mandiri untuk event di **https://vesta.halofans.id**.

Situsnya adalah aplikasi Next.js yang datanya diambil dari **dua API JSON**:

| API | Untuk | Contoh |
|-----|-------|--------|
| `api.halofans.id` | Event lama (ID numerik) | `/event/96402` |
| `spl.moflip.com` | Event "v2" (pakai slug) | `/event/v2/indo-comic` |

Script ini bicara langsung ke API tersebut — **tidak butuh browser**.

---

## 1. Persiapan (sekali saja)

Butuh **Python 3.8+**. Cek dengan:

```bash
python3 --version
```

Install dependency:

```bash
pip install -r requirements.txt
# atau:
pip install requests
```

---

## 2. Cara menjalankan

Format umum:

```bash
python3 scrape_halofans.py <perintah> [opsi]
```

Semua hasil tersimpan di folder **`output/`** (format JSON + CSV).

### Perintah yang tersedia

| Perintah | Fungsi |
|----------|--------|
| `list-v2` | Daftar SEMUA event v2 + resolve semua slug |
| `list-v2 --details` | ^ plus tarik detail lengkap + tiket tiap event |
| `v2 SLUG [SLUG...]` | Scrape event v2 tertentu (mis. `indo-comic`) |
| `extract-codes` | Cari semua kode undangan / compliment di event v2 |
| `watch` | **Deteksi event / kode baru** + kirim ke Telegram (kalau di-set) |
| `watch --notify` | ^ plus notifikasi desktop (macOS/Linux) |
| `watch --test-telegram` | Kirim pesan tes ke Telegram lalu berhenti |
| `export-excel` | Export semua event/tiket/kode ke satu file `.xlsx` |
| `search KATA` | Cari event lama berdasarkan keyword |
| `ids ID [ID...]` | Ambil event lama berdasarkan ID numerik |
| `crawl` | Sapu keyword untuk kumpulkan semua event ID lama |

### Contoh

```bash
# Paling sering dipakai: tarik semua event v2 lengkap dengan tiketnya
python3 scrape_halofans.py list-v2 --details

# Cari kode compliment/undangan di semua event
python3 scrape_halofans.py extract-codes

# Scrape satu event tertentu
python3 scrape_halofans.py v2 indo-comic

# Scrape beberapa slug sekaligus
python3 scrape_halofans.py v2 indo-comic indo-comic-com073

# Event lama (ID numerik)
python3 scrape_halofans.py ids 96402 112497
python3 scrape_halofans.py search "psim" --pages 2

# Bantuan lengkap
python3 scrape_halofans.py -h
python3 scrape_halofans.py v2 -h
```

---

## 3. File hasil (di `output/`)

**Event v2 (spl.moflip.com):**
- `v2_all_slugs.json` / `.csv` — daftar semua event v2 + slug + link
- `v2_summary.json` — ringkasan + tiket tiap event
- `v2_tickets.csv` — satu baris per tiket (harga, status, jadwal jual)
- `v2_invitation_codes.json` / `.csv` — hasil `extract-codes`
- `v2_<slug>.json` — dump mentah lengkap per event

**Event lama (api.halofans.id):**
- `all_event_ids.json` / `.csv` — semua event ID lama + link
- `events.json` / `.csv` — detail event lama

**Notifikasi & Excel:**
- `watch_state.json` — snapshot terakhir (jangan dihapus, dipakai `watch` untuk membandingkan)
- `watch_report.txt` — ringkasan perubahan terakhir
- `halofans_events.xlsx` — hasil `export-excel` (3 sheet: Events, Tickets, Codes)

---

## 4. Notifikasi event / kode baru (`watch`)

Cara kerja: setiap kali `watch` dijalankan, ia menyimpan snapshot semua event ke
`output/watch_state.json`. Saat dijalankan lagi, ia **membandingkan** dengan snapshot
lama dan melaporkan event / kode compliment yang **baru muncul**.

```bash
# Jalankan pertama kali (menyimpan baseline, belum ada notif)
python3 scrape_halofans.py watch

# Jalankan lagi nanti — akan memberitahu kalau ada yang baru
python3 scrape_halofans.py watch

# Sekalian kirim notifikasi desktop (macOS / Linux)
python3 scrape_halofans.py watch --notify

# Ikutkan juga event test/internal (default: diabaikan)
python3 scrape_halofans.py watch --include-test
```

Secara default event test/internal (mis. "LOAD TEST", "[Testing]", "-copy-") **diabaikan**.

#### Event tersembunyi (Compliment / Private Link)

Beberapa event **tidak muncul di listing publik** — mis. tiket Compliment, Private
Link, BRImo, Komunitas. Event ini hanya bisa diakses lewat link langsung, tapi
ID-nya berurutan, jadi `watch` otomatis **men-scan rentang ID** di sekitar listing
untuk menemukannya.

```bash
# default: scan 20 id di bawah & di atas rentang listing
python3 scrape_halofans.py watch

# perlebar jangkauan scan (mis. 60 id)
python3 scrape_halofans.py watch --scan-pad 60

# scan rentang ID spesifik
python3 scrape_halofans.py watch --scan-from 2350 --scan-to 2420

# matikan scan (hanya pakai listing publik — lebih cepat)
python3 scrape_halofans.py watch --no-scan
```

> Scan membuat `watch` sedikit lebih lama (beberapa menit) karena mengecek banyak
> ID. Untuk jadwal otomatis di RDP ini tidak masalah. Kalau mau cepat, pakai `--no-scan`.

### 📱 Notifikasi ke Telegram (bisa dibuka di HP, link langsung diklik)

Ini cara paling praktis: bot Telegram akan mengirim pesan ke kamu setiap ada
event/kode baru. Link event bisa langsung diklik, dan **code-nya bisa di-tap untuk copy**.

**Langkah setup (sekali saja, ~2 menit):**

1. **Buat bot** — di Telegram, chat ke [@BotFather](https://t.me/BotFather), ketik
   `/newbot`, ikuti langkahnya. Kamu akan dapat **token** seperti
   `123456789:AAE...xyz`.
2. **Dapatkan chat id** — chat ke bot barumu (kirim pesan apa saja dulu), lalu buka:
   `https://api.telegram.org/bot<TOKEN>/getUpdates` di browser. Cari angka di
   bagian `"chat":{"id":123456789}` — itu **chat id** kamu.
3. **Set kredensial** (pilih salah satu):

   *Cara A — environment variable (disarankan):*
   ```bash
   export TELEGRAM_BOT_TOKEN="123456789:AAE...xyz"
   export TELEGRAM_CHAT_ID="123456789"
   ```
   *Cara B — langsung di command line:*
   ```bash
   python3 scrape_halofans.py watch \
     --telegram-token "123456789:AAE...xyz" \
     --telegram-chat "123456789"
   ```

4. **Tes koneksi:**
   ```bash
   python3 scrape_halofans.py watch --test-telegram
   ```
   Kalau berhasil, kamu terima pesan "✅ Test dari scraper Halofans" di Telegram.

**Setelah itu, cukup jalankan `watch`** — kalau ada event/kode baru, otomatis
dikirim ke Telegram:
```bash
python3 scrape_halofans.py watch
```

Opsi terkait:
- `--no-telegram` — jangan kirim Telegram (walau token sudah di-set)
- `--test-telegram` — kirim pesan tes lalu berhenti

### Menjalankan otomatis (cek berkala tanpa manual)

**macOS / Linux (cron)** — cek tiap 30 menit. Ketik `crontab -e` lalu tambahkan:

```
*/30 * * * * cd /path/ke/folder && TELEGRAM_BOT_TOKEN="xxx" TELEGRAM_CHAT_ID="yyy" /usr/bin/python3 scrape_halofans.py watch >> output/watch.log 2>&1
```

> 💡 **Telegram jalan di semua OS** (termasuk Windows) karena tidak butuh desktop —
> ini cara notifikasi paling andal, apalagi kalau script jalan otomatis di server/PC.

**Windows (Task Scheduler):**
1. Buka *Task Scheduler* → *Create Basic Task*
2. Trigger: *Daily* / berulang tiap sekian jam
3. Action: *Start a program*
   - Program: `python`
   - Arguments: `scrape_halofans.py watch`
   - Start in: folder tempat script berada

> Catatan: notifikasi desktop (`--notify`) hanya jalan di macOS (butuh `osascript`)
> dan Linux (butuh `notify-send`). Di Windows, cek isi `output/watch_report.txt`
> atau `output/watch.log`.

---

## 5. Export ke Excel (`export-excel`)

```bash
pip install openpyxl        # sekali saja
python3 scrape_halofans.py export-excel
```

Menghasilkan `output/halofans_events.xlsx` dengan 3 sheet:
- **Events** — semua event (id, slug, status, apakah test, semua-gratis, kode, URL)
- **Tickets** — semua tiket (harga, ketersediaan, jadwal jual)
- **Codes** — event yang punya kode compliment/undangan

> Tanpa `openpyxl`, kamu tetap bisa buka file `.csv` di folder `output/` langsung dengan Excel.

---

## 6. Tentang kode Compliment / Undangan

Halaman `/event/v2/{slug}` yang berjudul **"- Compliment"** biasanya tiket
gratis (Rp0) yang butuh **kode undangan**. Kode valid tersimpan di data event
pada field `meta.custom_invitation.value`.

Jalankan `python3 scrape_halofans.py extract-codes` untuk menampilkan semua kode
yang ditemukan. Tipe pencocokan (`match_type`):
- `IS` — input harus **sama persis** dengan salah satu nilai
- `INCLUDE_START_WITH` — input harus **diawali** salah satu nilai
- `EXCLUDE_START_WITH` — input **tidak boleh diawali** nilai tsb (biasanya validasi NIK/nomor HP, bukan kode gratis)

---

## Catatan

- Data berasal dari API publik platform. Harap patuhi Terms of Service situs
  dan gunakan secara wajar — script sudah menyertakan jeda antar-request.
- Kalau ada event baru, cukup jalankan ulang `list-v2` / `extract-codes`.
