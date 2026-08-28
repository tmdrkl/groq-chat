const ALLOWED_ORIGINS = "*";

const corsHeaders = (extra = {}) => ({
  "Access-Control-Allow-Origin": ALLOWED_ORIGINS,
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, x-stats-key",
  ...extra,
});

const jsonHeaders = (status = 200) => ({
  ...corsHeaders({ "Content-Type": "application/json" }),
  "Status": status,
});

const RATE_LIMIT_WINDOW = 86400;

function badRequest(message = "Permintaan tidak valid") {
  return new Response(JSON.stringify({ error: message }), { status: 400, headers: jsonHeaders(400) });
}

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: corsHeaders({ "Content-Type": "application/json" }),
  });
}

async function hmacSign(key, data) {
  const enc = new TextEncoder();
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    enc.encode(key),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", cryptoKey, enc.encode(data));
  return btoa(String.fromCharCode(...new Uint8Array(sig))).replace(/=+$/, "");
}

function bearerToken(request) {
  const h = request.headers.get("Authorization") || "";
  return h.startsWith("Bearer ") ? h.slice(7) : "";
}

async function verifyToken(env, token, ip) {
  if (!token || !env.CHAT_PIN) return false;
  const dot = token.indexOf(".");
  if (dot < 1) return false;
  const exp = parseInt(token.slice(0, dot), 10);
  if (!Number.isFinite(exp) || exp < Math.floor(Date.now() / 1000)) return false;
  const expected = await hmacSign(env.CHAT_PIN, `${ip}:${exp}`);
  return token.slice(dot + 1) === expected;
}

function counter(env) {
  if (!env || !env.COUNTER) return null;
  const id = env.COUNTER.idFromName("global");
  return env.COUNTER.get(id);
}

async function counterCall(stub, path, body) {
  const res = await stub.fetch(`https://counter/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

export class ChatCounter {
  constructor(state, env) {
    this.state = state;
    this.maxChats = parseInt((env && env.MAX_CHATS_PER_DAY) || "50", 10) || 50;
  }

  async fetch(request) {
    const url = new URL(request.url);
    try {
      if (url.pathname === "/add") {
        const body = await request.json();
        const day = new Date().toISOString().slice(0, 10);
        const key = "st:" + day;
        const rec = (await this.state.storage.get(key)) || { count: 0, ptok: 0, ctok: 0, models: {} };
        rec.count += 1;
        rec.ptok += body.ptok || 0;
        rec.ctok += body.ctok || 0;
        const m = (rec.models[body.model] = rec.models[body.model] || { count: 0, ptok: 0, ctok: 0 });
        m.count += 1;
        m.ptok += body.ptok || 0;
        m.ctok += body.ctok || 0;
        await this.state.storage.put(key, rec);
        return jsonResponse({ ok: true });
      }

      if (url.pathname === "/rate") {
        const { ip } = await request.json();
        const key = "rl:" + ip;
        const now = Math.floor(Date.now() / 1000);
        const rec = await this.state.storage.get(key);
        let count = 0;
        if (rec && now - rec.ts < RATE_LIMIT_WINDOW) count = rec.count;
        if (count >= this.maxChats) return jsonResponse({ limited: true });
        await this.state.storage.put(key, { count: count + 1, ts: now });
        return jsonResponse({ limited: false, count: count + 1 });
      }

      if (url.pathname === "/loginlog") {
        const { ip, country } = await request.json();
        const list = (await this.state.storage.get("logins")) || [];
        list.push({ ip, country: country || "??", time: new Date().toISOString() });
        if (list.length > 20) list.splice(0, list.length - 20);
        await this.state.storage.put("logins", list);
        return jsonResponse({ ok: true });
      }

      if (url.pathname === "/stats") {
        const entries = await this.state.storage.list({ prefix: "st:" });
        const logins = (await this.state.storage.get("logins")) || [];
        const total = { count: 0, ptok: 0, ctok: 0, models: {} };
        const byDay = [];
        for (const [k, rec] of entries) {
          const day = k.slice(3);
          byDay.push({
            date: day,
            chats: rec.count || 0,
            promptTokens: rec.ptok || 0,
            completionTokens: rec.ctok || 0,
            tokens: (rec.ptok || 0) + (rec.ctok || 0),
          });
          total.count += rec.count || 0;
          total.ptok += rec.ptok || 0;
          total.ctok += rec.ctok || 0;
          for (const [model, v] of Object.entries(rec.models || {})) {
            const t = (total.models[model] = total.models[model] || { count: 0, ptok: 0, ctok: 0 });
            t.count += v.count || 0;
            t.ptok += v.ptok || 0;
            t.ctok += v.ctok || 0;
          }
        }
        byDay.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
        const models = Object.entries(total.models)
          .map(([model, v]) => ({ model, ...v }))
          .sort((a, b) => b.count - a.count);
        return jsonResponse({
          totalChats: total.count,
          totalTokens: total.ptok + total.ctok,
          promptTokens: total.ptok,
          completionTokens: total.ctok,
          avgTokensPerChat: total.count ? Math.round((total.ptok + total.ctok) / total.count) : 0,
          byDay: byDay.slice(0, 30),
          models,
          logins: logins.reverse(),
        });
      }

      return jsonResponse({ error: "Not found" }, 404);
    } catch (e) {
      return jsonResponse({ error: String(e) }, 500);
    }
  }
}

function forwardedStream(groqBody, model, onDone) {
  const reader = groqBody.getReader();
  const decoder = new TextDecoder();
  let sseBuffer = "";
  let promptTokens = 0;
  let completionTokens = 0;

  function parseLine(line) {
    const text = line.slice(5).trim();
    if (!text || text === "[DONE]") return;
    try {
      const json = JSON.parse(text);
      if (json && json.usage) {
        promptTokens = json.usage.prompt_tokens || 0;
        completionTokens = json.usage.completion_tokens || 0;
      }
    } catch {}
  }

  return new ReadableStream({
    async start(controller) {
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          controller.enqueue(value);
          sseBuffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
          let idx;
          while ((idx = sseBuffer.indexOf("\n")) >= 0) {
            const line = sseBuffer.slice(0, idx).trim();
            sseBuffer = sseBuffer.slice(idx + 1);
            if (line.startsWith("data:")) parseLine(line);
          }
        }
        sseBuffer += decoder.decode();
        if (sseBuffer.startsWith("data:")) parseLine(sseBuffer);
        try { await onDone(model, promptTokens, completionTokens); } catch {}
        controller.close();
      } catch (err) {
        try { controller.error(err); } catch {}
      }
    },
    cancel() {
      reader.cancel().catch(() => {});
    },
  });
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders() });
    }

    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/stats") {
      const clientIp = request.headers.get("CF-Connecting-IP") || "unknown";
      const trusted = await verifyToken(env, bearerToken(request), clientIp);
      if (!trusted) {
        return new Response(
          JSON.stringify({ error: "Unauthorized — masukkan PIN dulu via /login <PIN>" }),
          { status: 401, headers: jsonHeaders(401) }
        );
      }
      const stub = counter(env);
      if (!stub) return jsonResponse({ error: "Usage tracking disabled (no COUNTER binding)" }, 500);
      try {
        const stats = await counterCall(stub, "stats", {});
        return new Response(JSON.stringify(stats), { headers: jsonHeaders() });
      } catch (e) {
        return jsonResponse({ error: String(e) }, 500);
      }
    }

    if (request.method === "GET" && url.pathname === "/geo") {
      return new Response(JSON.stringify({
        ip: request.headers.get("CF-Connecting-IP") || "unknown",
        country: request.headers.get("CF-IPCountry") || "unknown",
      }), { headers: jsonHeaders() });
    }

    if (request.method === "GET" && url.pathname === "/models") {
      const apiKey = env.GROQ_API_KEY;
      if (!apiKey) {
        return jsonResponse({ error: "GROQ_API_KEY belum diatur di Worker" }, 500);
      }
      const groqRes = await fetch("https://api.groq.com/openai/v1/models", {
        headers: { Authorization: `Bearer ${apiKey}` },
      });
      const data = await groqRes.json();
      const models = Array.isArray(data.data) ? data.data.map((m) => m.id) : [];
      return jsonResponse({ models });
    }

    if (request.method === "POST" && url.pathname === "/login") {
      if (!env.CHAT_PIN) {
        return jsonResponse({ error: "Login tidak tersedia (CHAT_PIN belum diatur)" }, 500);
      }
      let body;
      try {
        body = await request.json();
      } catch {
        return badRequest("Body harus berupa JSON");
      }
      const pin = typeof body.pin === "string" ? body.pin : "";
      if (pin !== env.CHAT_PIN) {
        return new Response(JSON.stringify({ error: "PIN salah" }), { status: 401, headers: jsonHeaders(401) });
      }
      const ip = request.headers.get("CF-Connecting-IP") || "unknown";
      const exp = Math.floor(Date.now() / 1000) + 24 * 3600;
      const sig = await hmacSign(env.CHAT_PIN, `${ip}:${exp}`);
      const stub = counter(env);
      if (stub) {
        try { await counterCall(stub, "loginlog", { ip, country: request.headers.get("CF-IPCountry") || "??" }); } catch {}
      }
      return new Response(JSON.stringify({ token: `${exp}.${sig}`, expires: exp }), { headers: jsonHeaders() });
    }

    if (request.method === "POST" && url.pathname === "/chat") {
      const apiKey = env.GROQ_API_KEY;
      if (!apiKey) {
        return jsonResponse({ error: "GROQ_API_KEY belum diatur di Worker" }, 500);
      }

      let body;
      try {
        body = await request.json();
      } catch {
        return badRequest("Body harus berupa JSON");
      }

      const model = typeof body.model === "string" ? body.model : "openai/gpt-oss-120b";
      const messages = Array.isArray(body.messages) ? body.messages : null;
      if (!messages) {
        return badRequest("Field 'messages' harus berupa array");
      }

      const clientIp = request.headers.get("CF-Connecting-IP") || "unknown";
      const trusted = await verifyToken(env, bearerToken(request), clientIp);
      const stub = counter(env);
      if (!trusted && stub) {
        try {
          const rl = await counterCall(stub, "rate", { ip: clientIp });
          if (rl.limited) {
            return jsonResponse({ error: "Terlalu banyak percakapan hari ini. Coba lagi besok." }, 429);
          }
        } catch {
          // jangan blokir chat jika counter bermasalah
        }
      }

      const groqRes = await fetch("https://api.groq.com/openai/v1/chat/completions", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          model,
          messages,
          temperature: 0.7,
          max_tokens: 4096,
          stream: true,
        }),
      });

      if (!groqRes.ok) {
        const text = await groqRes.text();
        return jsonResponse({ error: text }, groqRes.status);
      }

      return new Response(forwardedStream(groqRes.body, model, async (m, ptok, ctok) => {
        try {
          if (stub) await counterCall(stub, "add", { model: m, ptok, ctok });
        } catch {}
      }), {
        headers: corsHeaders({ "Content-Type": "text/event-stream; charset=utf-8" }),
      });
    }

    return jsonResponse({ error: "Not found" }, 404);
  },
};