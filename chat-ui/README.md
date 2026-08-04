# MaintWiz Chat UI

A chat front end for the Convai `/convai/AI/chat-completion` endpoint.

**This is an add-on.** Nothing in the parent project is modified by it, and
deleting this directory removes it completely. It has its own dependencies, its
own image and its own compose file.

React 18 + Vite + TypeScript, no runtime dependencies beyond React. Built output
is ~49 KB gzipped, served by nginx.

---

## Why nginx, and not just `npm run dev` pointed at the API

The API has no `CORSMiddleware`, so a browser on a different origin is blocked.
Rather than adding CORS to the backend, nginx serves the UI **and** proxies
`/convai/` to it — so the browser only ever sees one origin and CORS never
applies. That is the only reason this needs a web server at all.

`vite.config.ts` does the same thing with a dev proxy for `npm run dev`.

## Run it

### Docker (how it is deployed)

```sh
cd chat-ui
docker compose -f docker-compose.chatui.yml up -d --build
```

Then open <http://localhost:3001>.

Port 3001 by default — 8080 is in use by Jenkins.

### Pointing it at the API

nginx proxies `/convai/` to `API_UPSTREAM`. The default reaches the API through
the host gateway, which works wherever the API publishes port 8000 — a container
on the same host, or `uvicorn` running directly:

| Where the API is | Setting |
|---|---|
| Same host (default) | `API_UPSTREAM=host.docker.internal:8000` |
| Another machine | `API_UPSTREAM=10.0.0.5:8000` |
| Same docker network | `API_UPSTREAM=convai-app:8000` + attach this container to that network |

```sh
API_UPSTREAM=10.0.0.5:8000 docker compose -f docker-compose.chatui.yml up -d
CHAT_UI_PORT=9000          docker compose -f docker-compose.chatui.yml up -d
```

This deliberately does **not** join the application's docker network. Doing so
failed with `network conv-ai_default declared as external, but could not be
found` whenever the application stack was not running or was named differently —
and it is unnecessary while the API publishes 8000 to the host.

### Local development

```sh
cd chat-ui
npm install
npm run dev                                   # API assumed at localhost:8000
VITE_API_TARGET=http://1.2.3.4:8000 npm run dev
```

## Configuration

`public/config.json` — read at page load, not baked into the bundle, and mounted
read-only in Docker. Edit it and reload; no rebuild.

```json
{
  "tenant_label": "Coromandel",
  "database_name": "coromandel",
  "user_id": "1278",
  "facm_code": ["PSGM-PNB", "CFVZ-RG1"]
}
```

`facm_code` is the tenant scoping list and can hold up to 2000 entries — the
production payload has ~1,469. Paste the full list here; the UI sends it
verbatim on every request.

---

## Things worth knowing

**Responses stream as `text/plain`.** Not SSE, not JSON. The client reads the
body with a `ReadableStream` reader, so text appears as it is generated.

**The thinking indicator only runs until the first token.** A cold question
takes 5–7 s because it runs classification, embedding, retrieval, SQL generation
and execution before answering. The staged labels are time-based, not a report
of real pipeline state, so they are worded as expectations.

**`chat_history` is a flat string the client assembles** — `"user: …, ai: …"`.
The backend parses it with a regex, and it is what the classifier reads to
detect follow-ups. `buildChatHistory()` in `src/api.ts` builds it and drops the
oldest turns to stay under the 10,000-character limit, above which the API
returns 422.

**No markdown rendering, deliberately.** The backend's response prompt requires
plain conversational text, so messages render with `white-space: pre-wrap`. That
avoids a markdown parser and the sanitiser that would have to come with it.

**There is no authentication** on the API or this UI. Do not expose either
outside a trusted network.

## Try it

1. "List the work orders created in the last 30 days"
2. "more" — pages through the results
3. Then a new question — should be answered, not treated as pagination

## Layout

```
src/
  api.ts                       streaming client, chat_history builder, error mapping
  App.tsx                      conversation state, send/stop, auto-scroll
  types.ts
  components/
    MessageBubble.tsx
    Composer.tsx               auto-grow, Enter to send, character limit
    ThinkingIndicator.tsx
  styles.css                   light/dark, no framework
nginx.conf                     serves the UI, proxies /convai, streaming enabled
Dockerfile                     node build -> nginx
docker-compose.chatui.yml      standalone, joins the existing network
```
