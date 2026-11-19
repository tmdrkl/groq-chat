# groq-chat worker

Cloudflare Worker behind the chat and contact form on [drkl.net](https://www.drkl.net).

Deployed at **https://groq-chat.tomx13.workers.dev** (account that owns the `tomx13` workers.dev subdomain).

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

## Secrets

| Secret | Purpose |
|--------|---------|
| `GROQ_API_KEY` | Groq chat completions + model list |
| `CHAT_PIN` | Owner PIN for `/login`, lifts the chat rate limit |
| `TELEGRAM_BOT_TOKEN` | Bot that delivers contact messages |
| `TELEGRAM_CHAT_ID` | Destination chat for contact messages |

Set them with `npx wrangler secret put <NAME>`, or from the dashboard under
**Workers & Pages → groq-chat → Settings → Variables**.

## Deployment notes

- Run `npx wrangler deploy` from this `web/` directory.
- Log in as the Cloudflare account that owns the `tomx13` workers.dev subdomain.
  A different account fails with error `10063` (no workers.dev subdomain) or
  creates a parallel worker under another URL, which the site will not use.
- `wrangler deploy` publishes the code; the GitHub push is only source backup.
- `MAX_CONTACTS_PER_DAY` and `MAX_CHATS_PER_DAY` live in `wrangler.toml`.

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
