# 💰 Budget Telegram Bot

Telegram bot yang otomatis mencatat transaksi ke Google Sheets menggunakan AI (Gemini Flash).  
Kirim foto struk atau ketik transaksi via chat → AI baca & kategorisasi → Google Sheets terupdate otomatis!

Hosting: **Vercel** (Serverless Python Function)

---

## 🗺️ Arsitektur

```
Telegram ──▶ Vercel (Flask Serverless) ──▶ Gemini 1.5 Flash (AI OCR & Parser)
                                       ──▶ Google Sheets API (gspread)
```

---

## 🚀 Panduan Setup & Deploy ke Vercel (Langkah demi Langkah)

### 1. Pastikan Environment Variables Terpasang di Vercel

Buka [Vercel Dashboard](https://vercel.com) → Pilih project `budget-telegram-bot` → **Settings** → **Environment Variables**.

Pastikan 4 variable ini sudah terdaftar untuk **Production**:

| Variable Name | Keterangan |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token dari @BotFather di Telegram |
| `GEMINI_API_KEY` | API Key dari [Google AI Studio](https://aistudio.google.com) |
| `GOOGLE_SHEET_ID` | `1bihj0mIqYCtzY1Vcr5j3yyZc6t2i4TSkCSKplvuV4i8` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Seluruh isi file JSON Google Service Account |

---

### 2. Pengaturan Root Directory di Vercel

Di Vercel Dashboard → **Settings** → **General**:
- Cari bagian **Root Directory**
- Pastikan nilainya **kosong** (atau `./`) karena seluruh file bot sekarang sudah berada di root repository.
- Klik **Save** jika ada perubahan.

---

### 3. Redeploy di Vercel

Masuk ke tab **Deployments** di Vercel Dashboard:
- Klik tombol **⋮** (tiga titik) di samping deployment terbaru
- Pilih **Redeploy**
- Tunggu hingga status berubah menjadi **Ready** (ikon hijau)

---

### 4. Verifikasi Kesehatan Bot

Buka link ini di browser:
- `https://<domain-vercel-kamu>.vercel.app/`
  *Harus tampil pesan: `Budget Bot is running!`*
- `https://<domain-vercel-kamu>.vercel.app/debug`
  *Untuk mengecek apakah semua token dan koneksi modul berstatus OK.*

---

### 5. Daftarkan Webhook Telegram

Buka URL ini di browser (cukup sekali saja):
```
https://<domain-vercel-kamu>.vercel.app/set_webhook
```

Jika sukses, Telegram akan merespons:
```json
{"ok": true, "result": true, "description": "Webhook was set"}
```

---

### 6. Coba di Telegram!

Buka bot kamu di Telegram dan ketik:
- `/start` — Sambutan awal bot
- `Beli kopi kenangan 25000` — Bot akan mengenali merchant, nominal, dan kategori
- Kirim foto struk belanjaan langsung ke chat!
