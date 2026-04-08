# 🎨 FRONTEND DIRECTOR: Next.js, Tailwind & Acid Design

> **Role:** Frontend Technical Lead
> **Domain:** React, Next.js, Styling, Animation, Mobile UX
> **Last Updated:** 2026-04-07

This file contains all frontend-specific rules, design system specifications, and mobile guidelines for the VectorBox project.

---

## 1. Technology Stack

### Core Framework
| Technology | Version | Purpose |
| :--- | :--- | :--- |
| **Next.js** | `16.1.6` | App Router, SSR, Static Optimization |
| **React** | `19.2.4` | Concurrent features (Suspense, Transitions) |
| **pnpm** | Latest | Package manager (**required**) |

### Styling & Animation
| Technology | Version | Purpose |
| :--- | :--- | :--- |
| **Tailwind CSS** | `4.1.18` | CSS-First Architecture (`@theme`) |
| **Animations** | Native CSS | Pure CSS keyframes in `globals.css` |
| **Framer Motion** | `12.34.0` | Complex transitions, hover states, carousel physics |
| **Utilities** | `tailwind-merge` v3 | Conflicts resolution (`cn` helper) |

---

## 2. The "Acid Design" System

> [!IMPORTANT]
> VectorBox uses a high-contrast **neon aesthetic** called "Acid Design".

### Color Palette
Defined via CSS variables in `globals.css` `@theme`.

```css
/* Primary Accent */
--color-primary: oklch(0.9 0.4 110); /* Toxic Green #CCFF00 */

/* Backgrounds */
--color-background: oklch(0.05 0 0); /* Deep Black */
--color-card: oklch(0.08 0 0);
```

### Typography
- **Primary Font:** `Space Mono` (monospace)
- **Usage:** Headers, navigation, accent text
- **Import:** Google Fonts (via `next/font`)

### Design Principles
1. **High Contrast:** Neon green on near-black backgrounds
2. **Minimal Chrome:** Focus on content, reduce UI decoration
3. **Glowing Effects:** Subtle shadows with accent color for interactive elements
4. **Dark Mode Native:** The design IS dark mode

### Component Examples
```jsx
// Button with Acid styling (using Tailwind v4 tokens)
<button className="bg-primary text-black font-mono hover:bg-primary/90">
  Click Me
</button>

// Card with overlay
<div className="bg-black/80 border border-primary/20 rounded-lg">
  {/* content */}
</div>

// Glowing accent
<span className="text-primary drop-shadow-[0_0_8px_var(--color-primary)]">
  VectorBox
</span>
```

---

## 3. Mobile-First Guidelines

> [!NOTE]
> VectorBox is designed with a **mobile-first** approach.

### Responsive Breakpoints
```css
/* Tailwind defaults */
sm: 640px   /* Small phones → larger phones */
md: 768px   /* Phones → tablets */
lg: 1024px  /* Tablets → laptops */
xl: 1280px  /* Desktops */
```

### Grid Adaptation
| Breakpoint | Grid Columns |
| :--- | :--- |
| `< 640px` (narrow phones) | 1 column |
| `640px - 1023px` (large phones/tablets) | 2 columns |
| `≥ 1024px` (desktop) | 4+ columns |

### Mobile Navigation
- **Pattern:** Full-screen overlay hamburger menu
- **Font:** `Space Mono` in neon green
- **Trigger:** Hamburger icon in header (replaces full nav)
- **Animation:** Slide-in from right with Framer Motion

### Touch Optimization
- **Feed Carousel:** Native touch-scroll (CSS `scroll-snap`)
- **Arrow Buttons:** Hidden on touch devices, enlarged on desktop
- **Tap Targets:** Minimum 44x44px touch areas

### Header Simplification (Mobile)
- **Show only:** **Logo** + **Hamburger Icon**
- **Hide:** Full navigation, search bar (moved to menu)

---

## 4. Build System

### Docker Configuration
- **Strategy:** Multi-Stage Build
- **Output Mode:** `output: 'standalone'` in `next.config.js`
- **Image Size:** Target ~150MB

### Production Build
```bash
cd frontend
pnpm build
# Creates .next/standalone for Docker
```

---

## 5. Security Rules (.npmrc)

> [!WARNING]
> Supply chain protection is mandatory.

### Required .npmrc Configuration
Location: `frontend/.npmrc`

```ini
engine-strict=true
frozen-lockfile=true
audit=true
ignore-scripts=false
save-exact=false
```

### Package Management
*   **pnpm** is strictly enforced. No `npm` or `yarn`.
*   Enforce safety via `.npmrc`. Dependabot handles release-age policies at the repository level. Local installs use `--no-frozen-lockfile`.

### API Client Security (v1.2)
- **No User IDs:** Do not pass `userId` to API client methods. The backend derives identity strictly from the secure `vectorbox_token` cookie to prevent IDOR vulnerabilities.

---

## 6. Component Architecture

### Key Components
| Component | File | Purpose |
| :--- | :--- | :--- |
| `magic-search.tsx` | `components/` | NLP search bar UI |
| `feed-container.tsx` | `components/` | Main scrollable feed |
| `right-console.tsx` | `components/` | Data Inspector and global filters console |
| `ProgressModal` | `components/` | Real-time task progress display |
| `AcidError` | `components/ui/` | Graceful, styled error boundary interceptor |

### Directory Structure
```
frontend/
├── app/           # Next.js App Router pages
├── components/    # React components
├── ui/            # Reusable primitives (buttons, dialogs)
├── lib/           # Utilities and helpers
└── messages/      # i18n translation files (en.json, es.json)
```

---

## 7. Web Quality Standards (Addy Osmani Toolkit)

> [!WARNING]
> All new frontend code MUST pass the Web Quality Baseline (Performance, Accessibility, Best Practices).

1. **Performance (Core Web Vitals):**
   - **LCP:** Eagerly load above-the-fold images (`priority`, `fetchPriority="high"`).
   - **INP:** Never use `useState` for high-frequency continuous events (like `mousemove`). Mutate DOM directly via `useRef` inside `requestAnimationFrame`.
   - **CLS:** Apply `display: "optional"` to all `next/font` imports.
2. **Accessibility:**
   - **Keyboard traps:** Use `e.target !== e.currentTarget` to prevent programmatic button events from bubbling up to parent containers.
   - **ARIA:** Wrap dynamic loading/empty states in `aria-live="polite"`. Add `role="dialog"` to modals.
   - **Contrast:** `text-zinc-400` is the absolute minimum lightness on a black background for small text.
3. **Best Practices:**
   - **Resilience:** All Next.js Server-Side fetch calls MUST wrap an `AbortController` with a strictly enforced timeout (e.g., 10s) to prevent hanging the internal worker threads.
   - **Hygiene:** Strip development `console.log` from interceptors and production error paths.

---

---

## 8. TypeScript Type Safety

> [!WARNING]
> TypeScript's `any` type is a build-time escape hatch that defeats the entire purpose of type checking. Every `as any` cast is a latent bug waiting to happen.

### Rules — No Type Erasure
- **Ban:** `(obj as any).field` — casting to `any` to access a field you know exists. **Fix:** add the missing field to the proper interface.
- **Ban:** `interface Foo { [key: string]: any }` — open index signatures on typed request/response shapes. Every field from the backend schema must be declared explicitly.
- **Ban:** `setState(x as any as TargetType)` — double-cast assignment. Ensure the source type is correctly shaped.

### Pattern — Keeping Interfaces in Sync with the Backend
When the backend adds or changes a field on a Pydantic schema, the corresponding TypeScript interface MUST be updated before using the field:

```typescript
// ❌ Accessing an undeclared field via cast
const hasData = (verifiedUser as any).has_data;

// ✅ Add the field to the interface
export interface AuthResponse {
    token: string;
    user_id: number;
    username: string;
    has_data?: boolean; // ← declared here
}
const hasData = verifiedUser.has_data;
```

### Pattern — Explicit Intent Interfaces
API request bodies that originate from structured backend schemas must mirror that schema exactly:

```typescript
// ❌ Open index signature — hides typos, breaks autocomplete
interface SearchIntent {
    summary?: string;
    [key: string]: any;
}

// ✅ All fields declared explicitly (matches MovieSearchIntent Pydantic model)
interface SearchIntent {
    semantic_query: string;
    reasoning: string;
    year_min?: number;
    year_max?: number;
    include_genres?: string[];
    popularity_vibe?: "blockbuster" | "hidden_gem" | "any";
    // ... remaining fields
}
```

### Pattern — Dead Fallback Removal
When the API contract guarantees a field name, remove fallback paths that access the old name via `as any`:

```typescript
// ❌ Dead fallback — API always returns poster_url, never poster_path
src={movie.poster_url || getTMDBImageUrl((movie as any).poster_path, "w342")}

// ✅ Use the guaranteed field
src={movie.poster_url}
```

---

*For architectural enforcement rules, see [architect.md](../c-suite/architect.md).*
