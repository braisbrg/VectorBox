"use client";

// The in-app replacement for <input type="color">. Same interaction as the
// native control — click the swatch, a panel opens, drag to pick — but rendered
// in the ACID kit instead of the OS dialog, which no CSS can reach.
//
// Built on the native popover API: light-dismiss, Escape and top-layer stacking
// come free. The cost is positioning — a popover is in the top layer, so an
// ancestor's `position: relative` does not place it. Hence the toggle handler.

import { useRef, useState } from "react";

/** HSV is what a square-and-rail picker is; hex is what everything else stores. */
function hsvToHex(h: number, s: number, v: number): string {
    const f = (n: number) => {
        const k = (n + h / 60) % 6;
        return Math.round(255 * (v - v * s * Math.max(0, Math.min(k, 4 - k, 1))));
    };
    return `#${[f(5), f(3), f(1)].map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

function hexToHsv(hex: string): [number, number, number] {
    const n = parseInt(hex.slice(1), 16);
    const r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
    const max = Math.max(r, g, b), d = max - Math.min(r, g, b);
    const h = d === 0 ? 0 : max === r ? 60 * (((g - b) / d) % 6) : max === g ? 60 * ((b - r) / d + 2) : 60 * ((r - g) / d + 4);
    return [(h + 360) % 360, max === 0 ? 0 : d / max, max];
}

const clamp = (x: number) => Math.min(1, Math.max(0, x));

export function AccentPicker({
    value,
    onChange,
    label,
    className,
}: {
    value: string;         // #rrggbb the panel opens on
    onChange: (hex: string) => void;
    label: string;
    className?: string;
}) {
    const pop = useRef<HTMLDivElement>(null);
    const btn = useRef<HTMLButtonElement>(null);
    const [[h, s, v], setHsv] = useState<[number, number, number]>(() => hexToHsv(value));

    const emit = (next: [number, number, number]) => {
        setHsv(next);
        onChange(hsvToHex(...next));
    };

    const onToggle = (e: React.ToggleEvent<HTMLDivElement>) => {
        if (e.newState !== "open") return;
        setHsv(hexToHsv(value)); // reopen on whatever the accent is now, not where we left the knob
        const r = btn.current!.getBoundingClientRect();
        const p = pop.current!;
        p.style.left = `${Math.min(r.left, window.innerWidth - 236)}px`;
        p.style.top = `${r.bottom + 6}px`;
        // fixed to the viewport, so a scroll would detach it from its swatch
        window.addEventListener("scroll", () => p.hidePopover(), { once: true, passive: true });
    };

    const pick = (e: React.PointerEvent<HTMLDivElement>) => {
        if (e.type === "pointermove" && !e.buttons) return;
        const r = e.currentTarget.getBoundingClientRect();
        emit([h, clamp((e.clientX - r.left) / r.width), 1 - clamp((e.clientY - r.top) / r.height)]);
    };

    const nudge = (e: React.KeyboardEvent) => {
        const d = { ArrowLeft: [-0.02, 0], ArrowRight: [0.02, 0], ArrowUp: [0, 0.02], ArrowDown: [0, -0.02] }[e.key];
        if (!d) return;
        e.preventDefault();
        emit([h, clamp(s + d[0]), clamp(v + d[1])]);
    };

    return (
        <>
            <button
                ref={btn}
                type="button"
                popoverTarget="accent-pop"
                aria-label={label}
                title={label}
                className={className}
                style={{ background: value }}
            />
            <div
                ref={pop}
                id="accent-pop"
                popover="auto"
                onToggle={onToggle}
                className="fixed m-0 w-[220px] border border-border-2 bg-bg-2 p-2.5 shadow-[4px_4px_0_0_var(--border)]"
            >
                <div
                    role="slider"
                    tabIndex={0}
                    aria-label={label}
                    aria-valuetext={`saturation ${Math.round(s * 100)}, brightness ${Math.round(v * 100)}`}
                    onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); pick(e); }}
                    onPointerMove={pick}
                    onKeyDown={nudge}
                    className="relative h-[120px] cursor-crosshair touch-none border border-border-2 outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
                    style={{
                        background: `linear-gradient(to top, #000, transparent), linear-gradient(to right, #fff, ${hsvToHex(h, 1, 1)})`,
                    }}
                >
                    <span
                        className="pointer-events-none absolute size-[11px] -translate-x-1/2 -translate-y-1/2 border border-black shadow-[0_0_0_1px_#fff]"
                        style={{ left: `${s * 100}%`, top: `${(1 - v) * 100}%`, background: value }}
                    />
                </div>
                <input
                    type="range"
                    min={0}
                    max={360}
                    value={h}
                    aria-label="hue"
                    onChange={(e) => emit([+e.target.value, s, v])}
                    className="acid-range hue-rail mt-2 w-full"
                />
            </div>
        </>
    );
}
