"use client";

// Pre-login splash — handoff screens-v3/pre-login.jsx, faithful build.
// Split-screen "higher-or-lower": LEFT = account track (GET STARTED zone with
// both CTAs + onboarding carousel preview), RIGHT = guest features that run
// without auth (magic box → /try/magic · more like this → /try/mlt · group rec
// → /try/group). Hovered zone expands, the rest dim. Logged-in → /feed.

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useUser } from "@clerk/nextjs";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useLanguage } from "@/components/language-provider";

interface Zone {
    id: string;
    label: string;
    sub: string;
    tone: string;
    href: string;
    tier: "auth" | "guest";
}

// label/sub are i18n keys (land.*), resolved at render
const LEFT_ONBOARDING: Zone = {
    id: "onboarding",
    label: "land.onboarding",
    sub: "land.onboarding_sub",
    tone: "#22241a",
    // → the start-selection (explore feed / rate films), not straight to explore.
    href: "/onboarding",
    tier: "auth",
};

const RIGHT_ZONES: Zone[] = [
    { id: "magic", label: "land.magic", sub: "land.magic_sub", tone: "#1c1f22", href: "/try/magic", tier: "guest" },
    { id: "mlt", label: "land.mlt", sub: "land.mlt_sub", tone: "#1a1c1a", href: "/try/mlt", tier: "guest" },
    // Group rec needs an account (signed-in requester) — marked account, not guest.
    { id: "group", label: "land.group", sub: "land.group_sub", tone: "#241c20", href: "/register", tier: "auth" },
];

function ZoneTile({ zone, hovered, setHovered }: { zone: Zone; hovered: string | null; setHovered: (v: string | null) => void }) {
    const { t } = useLanguage();
    const isHover = hovered === zone.id;
    const dim = hovered !== null && !isHover;
    return (
        <Link
            href={zone.href}
            onMouseEnter={() => setHovered(zone.id)}
            onMouseLeave={() => setHovered(null)}
            className="relative flex min-h-0 items-end overflow-hidden border-b border-r border-bg transition-all duration-200"
            style={{
                background: `linear-gradient(135deg, ${zone.tone} 0%, #050505 120%)`,
                opacity: dim ? 0.35 : 1,
                flex: isHover ? 1.4 : 1,
            }}
        >
            {isHover && <div className="pointer-events-none absolute inset-0 bg-primary opacity-[0.08]" />}
            <span
                className={cn(
                    "absolute left-3.5 top-3.5 font-display text-[9px] uppercase tracking-[0.18em]",
                    zone.tier === "auth" ? "text-primary" : "text-fg-3"
                )}
            >
                {zone.tier === "auth" ? t("land.tier_account") : t("land.tier_guest")}
            </span>
            {isHover && <span className="absolute right-3.5 top-3.5 font-display text-lg text-primary">→</span>}
            <div className="w-full px-5 py-3.5 lg:px-7 lg:py-6">
                <div
                    className={cn(
                        "mb-1.5 font-display leading-none tracking-[-0.02em] transition-all duration-200",
                        isHover ? "text-[clamp(26px,3.4vw,36px)] text-primary" : "text-[clamp(20px,2.6vw,28px)] text-fg"
                    )}
                >
                    {t(zone.label)}
                </div>
                <div className="max-w-[340px] font-mono text-xs leading-snug text-fg-2">{t(zone.sub)}</div>
            </div>
        </Link>
    );
}

export function Landing() {
    const router = useRouter();
    const { t } = useLanguage();
    const { isLoaded, isSignedIn } = useUser();
    const [hovered, setHovered] = useState<string | null>(null);

    useEffect(() => {
        if (isLoaded && isSignedIn) router.replace("/feed");
    }, [isLoaded, isSignedIn, router]);

    if (!isLoaded || isSignedIn) {
        return (
            <main className="flex min-h-screen items-center justify-center bg-bg">
                <Loader2 className="size-8 animate-spin text-primary" />
            </main>
        );
    }

    const authHover = hovered === "auth";
    const authDim = hovered !== null && !authHover;

    return (
        <main className="relative flex min-h-dvh flex-col bg-bg text-fg">
            {/* TOPBAR */}
            <header className="z-10 flex h-[54px] shrink-0 items-center justify-between border-b border-border-2 bg-bg px-5">
                <Link href="/" className="font-display text-base uppercase tracking-tight">
                    <span className="text-fg">VECTOR</span>
                    <span className="ml-0.5 bg-primary px-1.5 py-0.5 text-primary-ink">BOX</span>
                </Link>
                <nav className="flex gap-4 font-mono text-[11px] text-fg-3">
                    <Link href="/privacy" className="transition-colors hover:text-primary">{t("land.privacy")}</Link>
                    <Link href="/terms" className="transition-colors hover:text-primary">{t("land.terms")}</Link>
                </nav>
            </header>

            {/* SPLIT — auto-rows-fr: on mobile the two halves share ONE viewport
                (no scroll) instead of stacking 60dvh each. */}
            <div className="grid min-h-0 flex-1 auto-rows-fr grid-cols-1 lg:grid-cols-2">
                {/* LEFT — account (yellow divider anchors "the real product") */}
                <div className="flex min-h-0 flex-col lg:border-r-2 lg:border-primary">
                    {/* AUTH ZONE — both CTAs merged */}
                    <div
                        onMouseEnter={() => setHovered("auth")}
                        onMouseLeave={() => setHovered(null)}
                        className="relative flex min-h-0 flex-col justify-end overflow-hidden border-b border-r border-bg transition-all duration-200"
                        style={{
                            background: "linear-gradient(135deg, #2a2520 0%, #050505 120%)",
                            opacity: authDim ? 0.35 : 1,
                            flex: authHover ? 1.4 : 1,
                        }}
                    >
                        {authHover && <div className="pointer-events-none absolute inset-0 bg-primary opacity-[0.06]" />}
                        <span className="absolute left-3.5 top-3.5 font-display text-[9px] uppercase tracking-[0.18em] text-primary">
                            {t("land.tier_account")}
                        </span>
                        <div className="w-full px-5 py-3.5 lg:px-7 lg:py-6">
                            <div
                                className={cn(
                                    "mb-3.5 font-display leading-none tracking-[-0.02em] text-fg transition-all duration-200",
                                    authHover ? "text-[clamp(28px,3.6vw,38px)]" : "text-[clamp(24px,3vw,32px)]"
                                )}
                            >
                                {t("land.get_started")}
                            </div>
                            <div className="flex flex-wrap gap-2">
                                <Link
                                    href="/register"
                                    className="bg-primary px-4 py-2.5 font-display text-[11px] font-bold tracking-[0.1em] text-primary-ink"
                                >
                                    {t("land.create")}
                                </Link>
                                <Link
                                    href="/login"
                                    className="border border-fg px-4 py-2.5 font-display text-[11px] font-bold tracking-[0.1em] text-fg transition-colors hover:border-primary hover:text-primary"
                                >
                                    {t("land.signin")}
                                </Link>
                            </div>
                            <div className="mt-2.5 font-mono text-[10px] text-fg-3">
                                {t("land.auth_note")}
                            </div>
                        </div>
                    </div>
                    <ZoneTile zone={LEFT_ONBOARDING} hovered={hovered} setHovered={setHovered} />
                </div>

                {/* RIGHT — guest features */}
                <div className="flex min-h-0 flex-col">
                    {RIGHT_ZONES.map((z) => (
                        <ZoneTile key={z.id} zone={z} hovered={hovered} setHovered={setHovered} />
                    ))}
                </div>
            </div>

            {/* FOOTER */}
            <footer className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-border-2 bg-bg-2 px-5 py-2.5 font-mono text-[10px] text-fg-3">
                <span>{t("land.foot_engine")}</span>
                <span>{t("land.foot_guest")}</span>
                <span className="hidden sm:inline">{t("land.foot_vector")}</span>
            </footer>
        </main>
    );
}
