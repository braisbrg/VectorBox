"use client";

// Auth split-panel shell — handoff `renderAuth` (prototype routes in/up):
// LEFT brand panel (wordmark · tagline · 60-dot constellation, 12 lit · foot)
// RIGHT column renders the page's auth content.

import Link from "next/link";
import { Wordmark } from "@/components/ui/wordmark";
import { cn } from "@/lib/utils";
import { useLanguage } from "@/components/language-provider";

// Deterministic pseudo-random (same family as the prototype's hashStr).
function hash(s: string): number {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) {
        h ^= s.charCodeAt(i);
        h = Math.imul(h, 16777619);
    }
    return h >>> 0;
}

/** Static 60-dot constellation, 12 lit acid — deterministic, no animation. */
function Constellation() {
    const dots = Array.from({ length: 60 }).map((_, i) => {
        const h1 = hash(`auth-dot-${i}`);
        const h2 = hash(`auth-dot-y-${i}`);
        return { x: 4 + (h1 % 92), y: 4 + (h2 % 92), lit: i % 5 === 0 };
    });
    const lit = dots.filter((d) => d.lit);
    return (
        <svg viewBox="0 0 100 100" className="h-auto w-full max-w-[280px]" aria-hidden>
            {lit.slice(1).map((d, i) => (
                <line
                    key={i}
                    x1={lit[i].x}
                    y1={lit[i].y}
                    x2={d.x}
                    y2={d.y}
                    stroke="var(--fg-3)"
                    strokeWidth="0.3"
                    strokeDasharray="1.5 2"
                    opacity="0.5"
                />
            ))}
            {dots.map((d, i) =>
                d.lit ? (
                    <rect key={i} x={d.x - 1.4} y={d.y - 1.4} width="2.8" height="2.8" fill="var(--primary)" />
                ) : (
                    <rect key={i} x={d.x - 0.8} y={d.y - 0.8} width="1.6" height="1.6" fill="var(--fg-3)" opacity="0.55" />
                )
            )}
        </svg>
    );
}

/** Shared Clerk appearance — re-skins the ACTUAL Clerk form to the handoff auth
 * aesthetic (bordered mono fields, lime submit, zero radius, no glows). The page
 * supplies its own title, so Clerk's header is hidden. `variables` give concrete
 * acid values as a readable base (pre-login = acid theme); `elements` do the exact
 * look. Colors are concrete because Clerk computes hover/focus shades from them and
 * can't derive shades from oklch CSS vars. */
export const clerkAcidAppearance = {
    variables: {
        borderRadius: "0px",
        fontFamily: "'IBM Plex Mono', monospace",
        colorPrimary: "#f7e800",
        colorText: "#f2f2f2",
        colorTextSecondary: "#9a9a9a",
        colorBackground: "transparent",
        colorInputText: "#f2f2f2",
        colorInputBackground: "#141414",
        colorDanger: "#ff5c5c",
    },
    elements: {
        rootBox: "font-mono w-full",
        cardBox: "w-full shadow-none",
        card: "bg-transparent border-0 rounded-none shadow-none p-0 gap-4",
        // Inline style — a "hidden" className loses to Clerk's own CSS; the page
        // supplies its own title so Clerk's header must go.
        header: { display: "none" },
        socialButtonsBlockButton:
            "rounded-none border border-border-2 bg-bg-2 font-mono text-fg shadow-none hover:border-primary hover:bg-bg-3",
        socialButtonsBlockButtonText: "font-mono text-fg",
        dividerLine: "bg-border-2",
        dividerText: "font-mono text-[10px] uppercase tracking-widest text-fg-3",
        formFieldLabel: "font-mono text-[10px] uppercase tracking-wide text-fg-3",
        formFieldInput:
            "rounded-none bg-bg-3 border border-border-2 font-mono text-fg shadow-none focus:border-primary",
        formFieldInputShowPasswordButton: "text-fg-3 hover:text-primary",
        formFieldAction: "font-mono text-primary hover:underline",
        formButtonPrimary:
            "rounded-none border border-primary bg-primary text-primary-ink font-display font-bold uppercase tracking-widest shadow-none hover:bg-transparent hover:text-primary",
        footer: "bg-transparent",
        footerActionText: "font-mono text-fg-3",
        footerActionLink: "font-mono text-primary hover:underline",
        identityPreviewText: "font-mono text-fg",
        identityPreviewEditButton: "text-primary",
        formResendCodeLink: "text-primary",
        otpCodeFieldInput: "rounded-none border-border-2 bg-bg-3 font-mono text-fg",
        alert: "rounded-none border border-danger bg-bg-2",
        alertText: "font-mono text-fg",
    },
};

export function AuthSplit({ children }: { children: React.ReactNode }) {
    const { t } = useLanguage();
    return (
        <main className="grid min-h-dvh grid-cols-1 bg-bg text-fg lg:grid-cols-[minmax(0,480px)_1fr]">
            {/* LEFT — brand panel */}
            <aside className="relative hidden flex-col justify-between border-r border-border-2 bg-bg-2 p-10 lg:flex">
                <div>
                    {/* wordmark → landing (user 2026-07-10: escape hatch without browser-back) */}
                    <Link href="/" className="inline-block font-display text-2xl uppercase tracking-tight">
                        <Wordmark />
                    </Link>
                    <p className="mt-5 max-w-[300px] font-mono text-[12px] leading-relaxed text-fg-2">
                        {t("auth.tagline")}
                    </p>
                </div>
                <div className="py-8">
                    <Constellation />
                </div>
                <div className="font-mono text-[10px] uppercase tracking-[0.15em] text-fg-3">
                    trident engine · clerk-powered
                </div>
            </aside>

            {/* mobile mini-brand header */}
            <header className="flex h-[54px] items-center border-b border-border-2 bg-bg px-5 lg:hidden">
                <Link href="/" className="font-display text-base uppercase tracking-tight">
                    <Wordmark />
                </Link>
            </header>

            {/* RIGHT — page content */}
            <section className={cn("flex items-center justify-center p-6 lg:p-12")}>{children}</section>
        </main>
    );
}
