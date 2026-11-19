# groq-chat worker

Cloudflare Worker behind the chat and contact form on [drkl.net](https://www.drkl.net).

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/geo` | Caller IP + country |
| `GET` | `/models` | Available Groq chat models |
| `POST` | `/chat` | Streaming chat completion |
| `POST` | `/login` | Owner PIN → 24h token |
| `GET` | `/stats` | Usage stats (owner token) |
| `POST` | `/contact` | Forward a visitor message to Telegram |

## Contact setup

1. Create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`), then send it any message.
2. Read your chat ID from
   `https://api.telegram.org/bot<TOKEN>/getUpdates` (look for `message.chat.id`).
3. Store both values as Worker secrets:
   ```sh
   npx wrangler secret put TELEGRAM_BOT_TOKEN
   npx wrangler secret put TELEGRAM_CHAT_ID
   ```
4. Deploy:
   ```sh
   npx wrangler deploy
   ```
5. Test:
   ```sh
   curl -X POST https://groq-chat.tomx13.workers.dev/contact \
     -H 'Content-Type: application/json' \
     -d '{"message":"hello from curl","replyTo":"me@example.com"}'
   ```

`MAX_CONTACTS_PER_DAY` (default `5`) caps messages per IP per 24 hours.
The bot token never leaves the Worker.

## Local development

Requires Node.js and Wrangler.

```sh
cp .dev.vars.example .dev.vars   # fill in the values
npx wrangler dev
```

For local chat, also set `GROQ_API_KEY` in `.dev.vars`.

## Limits

- Messages: 1–2000 characters, 10 KB request body
- Replies: optional, max 200 characters
- Contact rate limit: 5 per IP per 24h (configurable)
- Chat rate limit: 50 per IP per 24h; owner PIN lifts it
