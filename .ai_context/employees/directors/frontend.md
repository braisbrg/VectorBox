# 🎨 FRONTEND DIRECTOR: Next.js, Tailwind & Acid Design

> **Role:** Frontend Technical Lead
> **Domain:** React, Next.js, Styling, Animation, Mobile UX
> **Last Updated:** 2026-02-18

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
# Block newly published packages (24h minimum age)
minimum-release-age=1440
engine-strict=true

# Safe exceptions (build tools)
minimum-release-age-exclude=browserslist caniuse-lite electron-to-chromium node-releases core-js-compat
```

### React 19 Peer Dependencies
Handle peer dependency warnings via `pnpm.overrides` in `package.json` to accept Next.js 16.1.6 patches.

---

## 6. Component Architecture

### Key Components
| Component | File | Purpose |
| :--- | :--- | :--- |
| `magic-search.tsx` | `components/` | NLP search bar UI |
| `feed-container.tsx` | `components/` | Main scrollable feed |
| `recommendation-grid.tsx` | `components/` | Movie card grid |
| `ProgressModal` | `components/` | Real-time task progress display |

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

*For architectural enforcement rules, see [architect.md](../c-suite/architect.md).*
