# LangGraph agent with real tools, streamed to assistant-ui

> 🇫🇷 **[Version française](README.fr.md)**

A **LangGraph (Python)** agent that calls real tools — Wikipedia, Hacker News, weather,
calculator — streamed over the **AI SDK UI Message Stream protocol** and rendered by
**assistant-ui** in a **Vite + React 19 + Tailwind 4 + shadcn/ui** front end. Conversations
are persisted in PostgreSQL, and the agent is reconfigurable from the UI without a restart.

[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.x-1C3C3C)](https://langchain-ai.github.io/langgraph/)
[![FastAPI](https://img.shields.io/badge/FastAPI-SSE-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![assistant-ui](https://img.shields.io/badge/assistant--ui-AI%20SDK%20protocol-000000)](https://www.assistant-ui.com/)
[![MCP](https://img.shields.io/badge/MCP-supported-6E56CF)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

All four tools are **free and keyless**. Only the LLM needs an account — and three of the
four supported providers have a free tier.

Everything runs in Docker with hot reload on both sides: **no Python or Node runtime is
required on the host**, and no external infrastructure is assumed. `git clone` then
`make dev`.

---

## What problem this solves

There is no Python equivalent of `@ai-sdk/langchain`. If you want a **LangGraph agent on
the backend** and **assistant-ui on the front end**, something has to speak the AI SDK
*UI Message Stream* protocol — token deltas, tool calls, tool results, errors — over SSE.

This repository is a working, tested implementation of that bridge (`protocol/stream.py`
and `protocol/messages.py`), plus everything a real chat app needs around it: conversation
history, per-tool toggles, runtime model switching, and MCP server discovery.

---

## Architecture

```
╔═══════════════ apps/web  ─ Vite 8 · React 19 · Tailwind 4 · shadcn · assistant-ui ═════════════════╗
║   <Thread/> + <ThreadList/>  ◀── usePersistentChatRuntime  ──▶  POST /api/chat                     ║
║   « xulux » shell : SidebarProvider · SidebarInset · navbar                    /api/threads/*      ║
╚═══════════════════════════════════════════════════════════════════════════════/api/settings/*═════╝
                                    │  SSE — UI Message Stream (x-vercel-ai-ui-message-stream: v1)
                                    ▼
╔═══════════════ apps/api  ─ Python 3.13 · FastAPI · LangGraph 1.x ══════════════════════════════════╗
║  to_lc_messages(UIMessage[])  →  graph.astream([messages, updates])  →  ui_message_stream()        ║
║                                                                                                    ║
║        ┌─────────┐   tool_calls ?   ┌─────────┐                                                    ║
║  START ─▶ agent  ├───── yes ───────▶│  tools  │──── ToolNode ───┐                                  ║
║        │ (LLM)   │◀─────────────────┴─────────┘                 │                                  ║
║        └────┬────┘   ReAct loop (configurable cap)              │                                  ║
║             │ no                 wikipedia · hacker_news · weather · calculator · MCP tools        ║
║             ▼                                                                                      ║
║            END                                                                                     ║
╚════════════════════════════════════════════════════════════════════════════════════════════════════╝
                                    │
                                    ▼
                  PostgreSQL 16  ─ database langgraph_poc
                  threads · messages · settings · mcp_servers
```

**Who does what**

| Component | Role |
|---|---|
| `langgraph` | The graph: state, nodes, conditional edges, ReAct loop |
| `langchain-core` (`@tool`) | Tool definitions; schemas derived from signatures + docstrings |
| `agent/protocol/stream.py` | Emits the AI SDK stream protocol (hand-written, see below) |
| `agent/protocol/messages.py` | Converts `UIMessage[]` history back into LangChain messages |
| `agent/api/threads.py` | Conversation persistence |
| `agent/core/settings.py` | Runtime configuration: tools, agent, model, MCP servers |
| `@assistant-ui/react` | The UI that speaks this protocol natively (text, tool calls, errors) |
| `shadcn/ui` | Visual primitives, cloned from the `v1-xulux` mockup |

### The four layers of `apps/api`

```
        api/         ──▶ core/  ──▶ infra/
     (FastAPI)          (agent)     (db, http, log)
          │                ▲
          └──▶ protocol/ ──┘
              (AI SDK)
```

Dependencies only point inward, and that is **verifiable**:

```bash
grep -rhoE "from agent\.(api|core|protocol|infra)" src/agent/core/   # → core, infra
grep -rhoE "from agent\.(api|core|protocol|infra)" src/agent/infra/  # → nothing
```

| Layer | Contents | What it guarantees |
|---|---|---|
| `api/` | FastAPI routers, request bodies, HTTP codes | **FastAPI exists only here.** `core/` never imports it once |
| `protocol/` | AI SDK protocol conversion and emission | swapping the front-end client touches this package only |
| `core/` | graph, model, tools, MCP, configuration | would run unchanged behind a CLI or a worker |
| `infra/` | Postgres, HTTP client, logging | knows nothing about the agent or the web |

**So why FastAPI?** LangGraph is a graph library, not a server: something has to expose
`/api/chat` over SSE, plus history and configuration CRUD. The alternative would be the
LangGraph Platform server (`langgraph dev`), which brings its own HTTP protocol and its own
storage — but here the front end (assistant-ui) dictates the AI SDK protocol, and
conversations live in our own tables. FastAPI keeps control of the wire, confined to a
~480-line layer.

---

## Quickstart

```bash
make install          # creates apps/api/.env and builds the images
# then fill in ONE key in apps/api/.env (see below)

make dev              # starts the stack in the background
```

Open <http://localhost:4311> — the root redirects to `/ichat`. Running `make` on its own
lists every target.

### Routes

| URL | Screen |
|---|---|
| `/ichat` | new conversation |
| `/ichat/c/:id` | existing conversation (deep-linkable, reloadable, shareable) |
| any other URL | `replaceState` to `/ichat` |

**No router.** With two routes, `history.pushState` + `popstate` are enough
(`lib/chat-route.ts` for URLs, `hooks/use-chat-route.ts` for state). The URL is the single
source of truth for the open conversation — it feeds the controlled `threadId` of
`useRemoteThreadListRuntime`, whose `onThreadIdChange` writes it back. Browser back/forward
therefore work with no dedicated code. The day you need nested routes, loaders, or
per-route code splitting, adopt a real router — not before.

In development the SPA fallback is Vite's own (`appType: 'spa'`, the default), so
`/ichat/c/x` returns `index.html`. **A static deployment must provide the same rewrite**
(`try_files $uri /index.html;` on nginx), otherwise reloading a conversation 404s.

| Target | Effect |
|---|---|
| `make dev` | starts the stack, hot reload on both sides |
| `make logs` | follows the logs — settings: [docs/logging.md](docs/logging.md) |
| `make stop` / `make down` | stops / removes the containers |
| `make test` | the API test suite, inside the container |
| `make test-unit` | same, minus the tests that hit external APIs |
| `make check` | tests + lint + front-end types + build |
| `make clean` | containers, volumes and build artifacts |

### Choosing an LLM

| `LLM_PROVIDER` | Key | Cost | Where to get it |
|---|---|---|---|
| `groq` *(default)* | `GROQ_API_KEY` | **free**, no credit card | <https://console.groq.com/keys> |
| `google` | `GOOGLE_API_KEY` | **free**, daily quota | <https://aistudio.google.com/apikey> |
| `ollama` | — | **free**, fully local | <https://ollama.com> |
| `openai` | `OPENAI_API_KEY` | pay as you go | <https://platform.openai.com/api-keys> |

Provider and model can also be changed **from the UI** (Configuration → Model) with no
restart. The keys stay in `apps/api/.env`: the API never exposes their value, only a
`has_key` boolean per provider.

`docker compose` reads `apps/api/.env` **when the container is created**, so after changing
a key run `make down && make dev` (a plain `restart` will not re-read it).

---

## Docker

Three services, self-contained: `git clone` then `make dev` is enough, no external
infrastructure is assumed.

| Service | Image | Reload |
|---|---|---|
| `api` | `python:3.13-slim` + `uv` | `uvicorn --reload` watches `/app/src`, bind-mounted from `apps/api/src` |
| `web` | `oven/bun:1.3-alpine` | Vite HMR, `apps/web` mounted, `host: true` to listen outside loopback |
| `db` | `postgres:16-alpine` | `db_data` volume; no published port, only the API reaches it |

The front end's `node_modules` lives in a named volume: without it, the `apps/web` bind
mount would shadow the one installed at image build time and Vite would refuse to start.
Corollary: after a `bun add`, recreate that volume
(`docker compose down && docker volume rm langgraph-poc_web_node_modules`).

The API waits for the database to be `service_healthy` before starting, so the first
`make dev` does not race schema creation.

**Pointing at an existing database.** `docker-compose.override.yml` is gitignored and loaded
automatically by compose: redefining `DATABASE_URL` there is enough to target a shared
PostgreSQL, and `db: profiles: ["disabled"]` switches off the bundled one (the API then
needs `depends_on: !reset []`). The repository stays self-contained for everyone else.

---

## The tools

All keyless, all called over plain HTTP, all with an 8-second timeout.

| Tool | Source | Why this one |
|---|---|---|
| `wikipedia_search` | Wikipedia REST API | stable for years, no quota |
| `hacker_news_search` | Algolia HN API | no key, no painful rate limit |
| `weather_forecast` | Open-Meteo (geocoding + forecast) | free for non-commercial use |
| `calculator` | local AST evaluator | zero network, zero dependency |

A deliberate choice: **no DuckDuckGo scraping**. It is what most LangChain demos do, and it
is the first thing that breaks — shifting HTML, rate limiting, blocked IPs. Four documented
public APIs beat a scraper that works today.

### The calculator

The expression comes from the LLM, which is itself steered by user input and by whatever
the web tools bring back: **that is untrusted data**. Rather than hardening an `eval()` (a
losing game), `tools/calculator.py` parses the expression and executes only explicitly
allow-listed AST nodes. Rejected by construction: attribute access, subscripting,
comprehensions, lambdas, free names, and any call outside the allow-list. Seven escape
attempts are covered by tests, including `__import__('os').system`,
`().__class__.__bases__`, and CPU exhaustion via `2 ** 10 ** 9`.

Difference from the TypeScript version: mathjs handled unit conversions (`90 km/h to m/s`),
this evaluator does not. The tool docstring tells the model to convert on its own
(`90 / 3.6`), which it does without trouble.

---

## Conversation history

The server **does not understand** message content: the assistant-ui client encodes each
message itself (`format` + `content`), and the server only stores and returns it. There are
therefore not two representations of the conversation to keep in sync — which is what makes
persistence safe here, whereas a server-side LangGraph checkpointer would have created a
second source of truth (and the restart / regeneration / edit bugs that come with it).

The REST contract is dictated by assistant-ui's `RemoteThreadListAdapter`:

```
GET    /api/threads?scope=          POST   /api/threads
GET    /api/threads/{id}            PATCH  /api/threads/{id}      DELETE /api/threads/{id}
GET    /api/threads/{id}/messages   POST   /api/threads/{id}/messages
```

The title is generated client-side from the first user message. Cascading deletes, isolated
scopes, upsert when a message is re-emitted (edit, regeneration).

---

## Configuration

Reachable from the sidebar (Configuration), four tabs:

- **Tools** — enable/disable each tool. A disabled tool is neither advertised to the model
  nor executable: it disappears from `bind_tools` **and** from the `ToolNode`.
- **Agent** — system prompt (fully replaces the default), loop cap (1..20), temperature (0..2).
- **Model** — provider and model. Keys never leave the API.
- **MCP** — CRUD for servers, whose tools are **discovered and bound to the model**
  (`agent/core/mcp.py`). An unreachable server is reported in the panel and skipped, without
  blocking startup or chat. Details: [docs/settings.md](docs/settings.md#serveurs-mcp).

Settings apply **without a restart**. That is the trap in this feature: the graph is cached,
and a stale cache would have made every setting inert until the container restarted.
`get_graph()` therefore compares a configuration version and rebuilds as soon as it changes;
a test asserts exactly that.

If Postgres is unavailable, the API serves defaults and chat keeps working — only history
goes dark (`/api/health` reports `history: false`).

---

## Project structure

```
apps/
  api/                      Python — 4 layers, dependencies pointing inward
    src/agent/
      main.py               wiring: FastAPI app, lifespan, router mounting
      api/                  ── HTTP surface (the ONLY layer importing FastAPI)
        chat.py               POST /api/chat (SSE) · GET /api/health
        threads.py            conversation history
        settings.py           /api/settings router: request bodies, 503, status codes
      core/                 ── the domain: the agent
        graph.py              StateGraph + context window + retry + cache
        model.py              model factory + catalogue + effort capabilities
        settings.py           configuration: models, snapshot, version, read/write
        mcp.py                tool discovery from MCP servers
        callbacks.py          run metrics (latency, tokens, tool duration)
        tools/                one file per tool
      protocol/             ── the AI SDK protocol, both directions
        messages.py           UIMessage[] -> LangChain messages
        stream.py             UI Message Stream emission
      infra/                ── technical bricks, no domain logic
        db.py                 asyncpg pool + schema
        http.py               HTTP client with timeout + result serialization
        log.py                logging configuration
    tests/                  89 tests
    Dockerfile
  web/
    src/
      components/ui/        primitives cloned from the v1-xulux mockup
      components/xulux/     shell: sidebar, navbar, logo, nav-user, theme
      components/settings/  configuration panel
      components/chat/      /ichat routing, model picker, context gauge
      components/assistant-ui/  generated by the shadcn registry, then vendored
      lib/                  routes, model memory, context estimation
docker-compose.yml
Makefile
```

---

## Implementation notes worth reading

The graph itself (context window, 429 retry, tool errors, MCP tools, metrics) has its own
note: [docs/graph.md](docs/graph.md).

**The bridge to the UI is hand-written.** On the TypeScript side, `@ai-sdk/langchain` did
the translation. It has no Python equivalent: `stream.py` and `messages.py` implement the
protocol directly. The format was not guessed — it was **captured on the wire** from the
TypeScript implementation before migrating (headers, chunk types, the `[DONE]` terminator,
the exact request body shape), then cross-checked against the `UIMessageChunk` type from the
`ai` package. The test payloads in `messages.py` are those captures.

**Two LangGraph stream modes in parallel**, each for what it does best: `messages` gives
tokens as they come (typing effect, tool arguments building up), `updates` gives consolidated
state as each node exits — that is where you read **parsed** arguments and tool results,
without reassembling partial JSON.

**The agent consumes `astream`, not `ainvoke`.** An `ainvoke` produces no intermediate
tokens: `stream_mode="messages"` would have nothing to forward and the UI would sit frozen
until the full response landed.

**Tool arguments must reach the client.** This was the most expensive bug in the TypeScript
version: the adapter emitted only the run id and the tool name. The client therefore stored
a call with no `input`; on the next turn, the reconverted history contained a `tool_call`
with no `function.arguments` and the API answered `400 INVALID_TOOL_RESULTS` — first message
fine, second one broken. Two tests lock this down, on emission and on replay.

**One UI assistant message carries both the call AND the response.** Chat APIs require the
sequence `AIMessage(tool_calls)` → `ToolMessage` → `AIMessage(text)`. So `messages.py`
splits one `UIMessage` into several LangChain messages, in part order. A tool call with no
result is dropped: without its matching `ToolMessage`, the upstream request fails.

**A tool never raises.** `safe_tool()` serializes the error to JSON so the LLM can read it
and recover, instead of aborting the run.

**Vendored components.** `components/ui/` and `components/xulux/` are cloned from the
`ux-ui-unified/v1-xulux` mockup; `components/assistant-ui/` comes from the shadcn registry.
Deliberate deviations, commented where they occur: no router here (so `usePathname` became a
prop and `next/link` an anchor), `next-themes` replaced by a local hook, the cmdk search
replaced by the "new conversation" action, and the header's sidebar trigger removed because
it duplicated the navbar's. Local additions: a `pointer-coarse:` variant to reach 44px touch
targets without inflating the mouse UI, and a `no-scrollbar` utility. Re-running `shadcn add`
will overwrite these patches.

**No CORS in development.** Vite proxies `/api` to the `api` service by its hostname on the
Docker network, so the front end calls a same-origin URL.

---

## Tests

```bash
make test        # 89 tests in the container
make test-unit   # 85 tests, no network calls
```

The graph tests go through `ui_message_stream()` — that is, **the exact endpoint path**:
only the network call to the LLM is replaced, by a fake model that streams real
`tool_call_chunks`. The tools themselves genuinely execute, and the history and configuration
tests hit the real database.

This is a lesson from the TypeScript version: its tests rebuilt their own stream instead of
calling production code, and stayed green while multi-turn was broken. A test that does not
exercise the production path tests nothing.

---

## Known limitations

- **MCP tools cannot be filtered individually**: only the per-server switch exists, whereas
  the project's own tools can be toggled one by one.
- **No LangGraph checkpointer**: an interrupted run is not resumed, and human approval before
  a tool call (`interrupt()`) is out of reach. See [docs/graph.md](docs/graph.md).
- **No authentication**: every route is open and configuration is global. Fine locally,
  unacceptable exposed.
- **Development images only**: the API Dockerfile ships the test tooling and mounts the code;
  a production image would need a multi-stage build without `--reload`.
- **The composer's attachment button** is rendered by assistant-ui, but the backend does not
  process attachments.
- **Prompt injection**: a Wikipedia page or an HN title can carry instructions the model will
  follow. Harmless here (every tool is read-only), but it must be addressed before adding any
  tool that writes. **The issue becomes real with MCP**: a third-party server brings tools
  whose code and side effects you do not control.

---

## License

[MIT](LICENSE) — this is a proof of concept, not a maintained product. Fork it, take the
`protocol/` bridge, and go.
