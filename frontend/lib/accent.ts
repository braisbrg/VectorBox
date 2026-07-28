/**
 * Resolving CSS custom properties that <canvas> cannot read.
 *
 * Canvas takes literal strings, not CSS variables, so anything drawn to a canvas
 * has to resolve the design tokens itself. Two traps, both of which have already
 * bitten this codebase:
 *
 *  1. getComputedStyle serialises oklch() AS oklch() — a regex for "rgb(r,g,b)"
 *     mis-parsed it and painted acid dark blue instead of #f7e800.
 *  2. Hardcoding the hex freezes one theme. There are six (acid/cyan/magenta/
 *     orange/bone/blood) and they resolve to six different colours.
 *
 * Painting one pixel and reading it back handles every colour syntax the
 * browser knows, including whatever a future theme uses.
 */

/** The active theme's --primary as an exact sRGB hex, e.g. "#f7e800". */
export function resolveAccent(): string {
    const probe = document.createElement("div");
    probe.style.color = "var(--primary)";
    probe.style.position = "absolute";
    probe.style.visibility = "hidden";
    document.body.appendChild(probe);
    const col = getComputedStyle(probe).color;
    probe.remove();
    const cv = document.createElement("canvas");
    cv.width = cv.height = 1;
    const ctx = cv.getContext("2d")!;
    ctx.fillStyle = col;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
    return `#${[r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

/**
 * The accent with an alpha channel, for gradient stops. Takes the already
 * resolved hex rather than re-probing, so it is safe to call inside a raf loop.
 */
export function accentRgba(accent: string, alpha: number): string {
    const n = parseInt(accent.slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

/**
 * The display font family, as canvas needs it.
 *
 * next/font generates its OWN family name — the display face resolves to
 * "martian", never "Martian Mono". Canvas code that hardcoded 'Departure Mono'
 * therefore never matched anything and silently fell through to IBM Plex Mono,
 * so every share card has been rendering in the body face rather than the
 * display one. Reading the token means the canvas follows whatever face
 * layout.tsx loads, including the next one.
 */
export function resolveDisplayFont(): string {
    const v = getComputedStyle(document.documentElement).getPropertyValue("--font-display").trim();
    return v || "ui-monospace, monospace";
}
