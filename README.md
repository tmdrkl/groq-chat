# groq-chat

AI chatbot berbasis [Groq](https://groq.com) — dua bentuk: CLI lokal (Python) dan Cloudflare Worker (untuk web terminal di [drkl.my.id](https://drkl.my.id)).

## Struktur

```
chatbot.py           CLI chatbot (Groq), riwayat di history.json
rag.py               pencarian RAG di handbooks (void-docs submodule)
config.json          konfigurasi CLI (kunci API dari .env)
requirements.txt     dependensi Python
web/
  worker.js          Cloudflare Worker: proxy chat + usage tracking
  wrangler.toml      config binding & limits
handbooks/void-docs  submodule: repo void-linux/void-docs utk RAG
```

## Cloudflare Worker

Dideploy di `https://groq-chat.tomx13.workers.dev`, dipakai frontend `tmdrkl.github.io` (drkl.my.id).

### Endpoint

| Method | Path | Keterangan |
|--------|------|------------|
| `POST`  | `/chat` | Proxy streaming ke Groq. Tanpa token valid → rate limit 50 chat/24 jam per IP; dengan token → bebas |
| `POST`  | `/login` | `{pin}` cocok dengan secret `CHAT_PIN` → token HMAC 24 jam terikat IP |
| `GET`   | `/stats` | Usage (total, per hari, per model, login history). Wajib token |
| `GET`   | `/geo` | `{ip, country}` dari header Cloudflare, tanpa pihak ketiga |
| `GET`   | `/models` | Daftar model Groq |
| `OPTIONS` | — | CORS |

### Setup & deploy

```sh
cd web
wrangler login
wrangler secret put GROQ_API_KEY   # kunci API Groq
wrangler secret put CHAT_PIN       # PIN owner
npx wrangler deploy
```

`MAX_CHATS_PER_DAY` (default `50`) bisa diubah di `wrangler.toml`.

### Penyimpanan

Durable Object `ChatCounter` (binding `COUNTER`):
- `st:{YYYY-MM-DD}` — usage per hari: jumlah chat, prompt/completion token, breakdown per model
- `rl:{ip}` — rate limit per IP (window 24 jam)
- `logins` — riwayat login `{ip, country, time}`, maksimal 20 entri

### Catatan runtime Cloudflare yang pernah menyulitkan

- **KV eventual consistency** antar-colo → `/stats` bisa terlihat kosong sesaat; karena itu digunakan Durable Objects (konsisten + atomik).
- **`ReadableStream.start()`**: kode setelah `controller.close()` tidak pernah dijalankan — panggil callback penutup (usage capture) *sebelum* `close()`.
- Saat mock lokal `stub.fetch()`: di produksi menerima `Request` asli; di test harus dibungkus `new Request(url, init)`.

## CLI

```sh
python bot.py            # mulai chat
python rag.py <kueri>    # cari konteks di handbooks
```

Konfigurasi di `config.json` (kunci API di `.env`), riwayat masuk `.gitignore`.

## Lisensi

MIT