# VectorBox — Frontend

Next.js 16 (App Router) + React 19, Tailwind v4 in CSS-first mode, Clerk for auth.
The visual language is "ACID": brutalist, zero border-radius, hard shadows, and a
measured acid yellow (`#f7e800`).

## Running it

```bash
docker compose up -d --build frontend      # from the repository root
```

Rebuilding is not optional after a frontend change — the container serves a
production build, so edits do **not** hot-reload into it. A passing `tsc` is not
a passing render; that mistake shipped a landing change that had never once
executed.

For a fast local loop instead:

```bash
cd frontend
pnpm install                               # pnpm, not npm — pnpm-lock.yaml is the lockfile
pnpm dev
```

Open http://localhost:3000. The API is proxied to the backend at :8000.

## Checks

```bash
pnpm exec tsc --noEmit
```

Both locales must stay at parity: every key in `messages/en.json` needs its twin
in `messages/es.json`.

## Design system

Tokens live in `app/globals.css` as CSS custom properties, redefined per theme —
six themes ship, selected via `data-theme` on the root element. Four rules the
components follow, and breaking them shows immediately:

- hard shadow only on things that float above the page
- stacked panels are welded (`border-t-0`), never gapped
- hover changes colour, not position
- the 1px nudge is for buttons only

Read the tokens from `globals.css`; never hardcode a hex. Five of the six themes
were once broken by a single hardcoded accent.

## Environment

```
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=...
```

Clerk's `clerkMiddleware` protects routes and `AuthBridge` attaches the JWT as a
Bearer token. The `vectorbox_token` cookie is a legacy fallback only.
