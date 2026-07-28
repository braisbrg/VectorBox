"use client";

// Pre-login landing — Fase 2 of the landing plan (2026-07-28).
//
// It replaces the five-zone "higher or lower" splash, which never said what the
// product does: a cold visitor met a menu of destinations labelled MAGIC BOX,
// ONBOARDING and "centroid blend", and had to click one to find out what any of
// it meant. Every one of those strings is gone.
//
// The page demonstrates instead of describing. One sentence in, twelve films
// out, and the account ask comes AFTER the value rather than in front of it.
// The results are real engine output served from `/api/search/showcase` — a pure
// cache reader, so default landing traffic never reaches Groq and the set of
// possible inputs stays closed while `/search/natural` is still open to free
// text. The field is a doorway to the real Magic Box (/try/magic) rather than a
// second search implementation, and it carries the same two modes the component
// has: ◐ a sentence, ⌕ a title.
//
// Logged-in visitors never see this; page.tsx redirects them to /feed.

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useUser } from "@clerk/nextjs";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { getShowcase, type ShowcaseSlug } from "@/lib/api";
import { MovieCard } from "@/components/ui/movie-card";
import { Wordmark } from "@/components/ui/wordmark";
import { LanguageToggle } from "@/components/language-toggle";
import { useLanguage } from "@/components/language-provider";
import { cn } from "@/lib/utils";

// Each chip is a slug the backend already knows; a visitor cannot type one in.
// That closed set is the whole security model of the showcase endpoint.
const CHIPS: { slug: ShowcaseSlug; key: string }[] = [
    { slug: "grief", key: "land.ph_phrase" },
    { slug: "heist70", key: "land.chip_heist" },
    { slug: "with-parents", key: "land.chip_parents" },
];

export function Landing() {
    const router = useRouter();
    const { t, language } = useLanguage();
    const { isLoaded, isSignedIn } = useUser();

    const [slug, setSlug] = useState<ShowcaseSlug>("grief");
    const [mode, setMode] = useState<"phrase" | "title">("phrase");
    const railRef = useRef<HTMLDivElement>(null);
    const [edges, setEdges] = useState({ left: false, right: false });

    useEffect(() => {
        if (isLoaded && isSignedIn) router.replace("/feed");
    }, [isLoaded, isSignedIn, router]);

    const { data, isLoading, isError } = useQuery({
        queryKey: ["showcase", slug, language],
        queryFn: () => getShowcase(slug, language),
        staleTime: 60 * 60 * 1000, // upstream caches the answer for a week
        retry: false,              // a 503 means "not warmed"; retrying will not help
        enabled: mode === "phrase",
    });

    // Rail edges drive the arrows. Same split as movie-carousel.tsx: arrows on
    // pointer devices, drag on phones.
    const syncEdges = () => {
        const el = railRef.current;
        if (!el) return;
        const max = el.scrollWidth - el.clientWidth;
        setEdges({ left: el.scrollLeft > 2, right: el.scrollLeft < max - 2 });
    };
    useEffect(syncEdges, [data]);

    const scrollRail = (dir: -1 | 1) => {
        const el = railRef.current;
        if (el) el.scrollBy({ left: (dir * el.clientWidth) / 2, behavior: "smooth" });
    };

    if (!isLoaded || isSignedIn) {
        return (
            <main className="flex min-h-dvh items-center justify-center bg-bg">
                <Loader2 className="size-8 animate-spin text-primary" />
            </main>
        );
    }

    const films = data?.results ?? [];
    const phrase = mode === "phrase";

    return (
        <main className="relative flex min-h-dvh flex-col bg-bg text-fg">
            <header className="flex h-12 shrink-0 items-center justify-between border-b border-border-2 px-5">
                <Link href="/" className="font-display text-base uppercase tracking-tight">
                    <Wordmark />
                </Link>
                <nav className="flex items-center gap-3 font-display text-[10px] uppercase tracking-[0.1em] text-fg-3">
                    <Link href="/try/mlt" className="hidden transition-colors hover:text-primary sm:inline">
                        {t("land.how")}
                    </Link>
                    <LanguageToggle />
                    <Link
                        href="/login"
                        className="border border-border-2 px-3 py-1.5 text-fg transition-colors hover:border-primary hover:text-primary"
                    >
                        {t("land.signin")}
                    </Link>
                </nav>
            </header>

            {/* ── the one gesture ─────────────────────────────────────────── */}
            <section className="flex flex-1 flex-col items-center justify-center px-4 py-10 text-center sm:px-6">
                <p className="font-display text-[9.5px] uppercase tracking-[0.22em] text-fg-3">
                    {t("land.eyebrow")}
                </p>
                <h1 className="mt-5 max-w-[19ch] text-balance font-display text-[clamp(20px,3.1vw,36px)] font-bold uppercase leading-[1.1] tracking-[-0.045em]">
                    {t("land.h1a")}{" "}
                    <mark className="bg-primary px-[7px] text-primary-ink">{t("land.h1b")}</mark>
                </h1>

                {/* A doorway, not a form: it links through to the real Magic Box
                    instead of shipping a second search implementation here. */}
                <div className="mt-7 w-full max-w-[680px] text-left">
                    <Link
                        href={phrase ? "/try/magic" : "/try/mlt"}
                        className="group flex items-center gap-2.5 border-b border-border-2 px-0.5 pb-3 pt-2.5 transition-colors hover:border-fg"
                    >
                        <span className="font-display text-[15px] text-primary">{phrase ? "◐" : "⌕"}</span>
                        <span className="flex-1 truncate font-mono text-[17px] text-fg-3">
                            {phrase ? t("land.ph_phrase") : t("land.ph_title")}
                        </span>
                        <span
                            aria-hidden
                            className="grid size-[26px] shrink-0 place-items-center border border-border-2 font-display text-[13px] text-fg-2 transition-colors group-hover:border-primary group-hover:bg-primary group-hover:text-primary-ink"
                        >
                            →
                        </span>
                    </Link>

                    <div className="mt-3 flex flex-wrap items-center justify-center gap-1.5">
                        <button
                            onClick={() => setMode("phrase")}
                            aria-pressed={phrase}
                            className={cn(
                                "border px-2.5 py-1 font-display text-[10px] uppercase tracking-[0.05em] transition-colors",
                                phrase ? "border-primary text-primary" : "border-border-2 text-fg-2 hover:text-fg"
                            )}
                        >
                            ◐ {t("land.mode_phrase")}
                        </button>
                        <button
                            onClick={() => setMode("title")}
                            aria-pressed={!phrase}
                            className={cn(
                                "border px-2.5 py-1 font-display text-[10px] uppercase tracking-[0.05em] transition-colors",
                                !phrase ? "border-primary text-primary" : "border-border-2 text-fg-2 hover:text-fg"
                            )}
                        >
                            ⌕ {t("land.mode_title")}
                        </button>
                        <span className="ml-1 max-w-full font-mono text-[10px] text-fg-3">
                            {phrase ? t("land.hint_phrase") : t("land.hint_title")}
                        </span>
                    </div>

                    {phrase && (
                        <div className="mt-3 flex flex-wrap justify-center gap-1.5">
                            {CHIPS.map((c) => (
                                <button
                                    key={c.slug}
                                    onClick={() => setSlug(c.slug)}
                                    aria-pressed={slug === c.slug}
                                    className={cn(
                                        "max-w-full truncate border px-2.5 py-1 font-mono text-[10px] transition-colors",
                                        slug === c.slug
                                            ? "border-primary text-primary"
                                            : "border-border-2 text-fg-3 hover:border-fg-3 hover:text-fg-2"
                                    )}
                                >
                                    {t(c.key)}
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            </section>

            {/* ── what the engine actually answered ───────────────────────── */}
            {phrase && (
                <section className="shrink-0 border-t border-border-2 bg-bg-2 px-5 pb-4 pt-3.5">
                    <div className="mb-2.5 flex items-center justify-center gap-3.5 font-display text-[9px] uppercase tracking-[0.18em] text-fg-3">
                        <span>
                            {t("land.results")} ·{" "}
                            <b className="font-normal text-primary">
                                {t("land.results_n").replace("{n}", String(films.length))}
                            </b>{" "}
                            · ◌ {t("land.no_account")}
                        </span>
                        {films.length > 0 && (
                            <span className="hidden gap-1.5 md:flex">
                                <button
                                    onClick={() => scrollRail(-1)}
                                    disabled={!edges.left}
                                    aria-label={t("land.prev")}
                                    className="grid size-[26px] place-items-center border border-border-2 text-fg-3 transition-colors hover:border-primary hover:text-primary disabled:opacity-30 disabled:hover:border-border-2 disabled:hover:text-fg-3"
                                >
                                    ‹
                                </button>
                                <button
                                    onClick={() => scrollRail(1)}
                                    disabled={!edges.right}
                                    aria-label={t("land.next")}
                                    className="grid size-[26px] place-items-center border border-border-2 text-fg-3 transition-colors hover:border-primary hover:text-primary disabled:opacity-30 disabled:hover:border-border-2 disabled:hover:text-fg-3"
                                >
                                    ›
                                </button>
                            </span>
                        )}
                    </div>

                    {isLoading && (
                        <>
                            <div className="flex justify-center gap-2.5 overflow-hidden">
                                {Array.from({ length: 8 }).map((_, i) => (
                                    <div key={i} className="aspect-[2/3] w-[122px] shrink-0 animate-pulse border border-border bg-bg-3" />
                                ))}
                            </div>
                            <p className="mt-3 text-center font-display text-[10px] uppercase tracking-[0.16em] text-fg-3">
                                {t("land.st_load")}
                            </p>
                        </>
                    )}

                    {isError && (
                        <p className="mx-auto max-w-[46ch] py-6 text-center font-mono text-[12.5px] leading-relaxed text-fg-2">
                            {t("land.st_err")}
                        </p>
                    )}

                    {!isLoading && !isError && films.length === 0 && (
                        <p className="mx-auto max-w-[46ch] py-6 text-center font-mono text-[12.5px] leading-relaxed text-fg-2">
                            {t("land.st_empty")}
                        </p>
                    )}

                    {films.length > 0 && (
                        <div
                            ref={railRef}
                            onScroll={syncEdges}
                            // Phones get a grid, desktops a rail. A horizontal rail on
                            // a phone hides most of the proof behind a gesture with no
                            // affordance (the arrows are pointer-only), and the results
                            // ARE the argument this page makes — so on small screens
                            // they all stay visible and the page scrolls vertically,
                            // which is the gesture the device already has.
                            //
                            // On the rail, `safe center` matters: plain `center`
                            // overflows on BOTH sides and puts the first poster out of
                            // reach. The top padding gives the hover lift somewhere to
                            // go, since overflow-x forces overflow-y to auto as well.
                            className="-mt-2 grid grid-cols-3 gap-2.5 pt-2 sm:grid-cols-4 md:flex md:snap-x md:snap-mandatory md:overflow-x-auto md:[justify-content:safe_center] scrollbar-hide"
                        >
                            {films.map((f) => (
                                <div key={f.movie_id} className="w-full md:w-[122px] md:shrink-0 md:snap-start">
                                    <MovieCard
                                        id={f.movie_id}
                                        title={f.title}
                                        title_es={f.title_es}
                                        posterPath={f.poster_path}
                                        year={f.year}
                                        runtime={f.runtime}
                                        genres={f.genres}
                                        vectorbox_score={f.vectorbox_score}
                                        forceVectorBoxScore
                                    />
                                </div>
                            ))}
                        </div>
                    )}
                </section>
            )}

            {/* ── the ask, after the value ────────────────────────────────── */}
            <section className="grid shrink-0 border-t border-border-2 md:grid-cols-2">
                <div className="flex flex-col items-start gap-2.5 p-5 md:border-r md:border-border-2">
                    <span className="font-display text-[8.5px] uppercase tracking-[0.18em] text-primary">
                        {t("land.tier_guest")}
                    </span>
                    <p className="max-w-[46ch] font-mono text-[12px] leading-relaxed text-fg-2">
                        <b className="font-normal text-fg">{t("land.conv_lead")}</b> {t("land.conv_sub")}
                    </p>
                    <div className="flex flex-wrap items-center gap-2.5">
                        <Link
                            href="/register"
                            className="border border-primary bg-primary px-4 py-2.5 font-display text-[10.5px] font-bold uppercase tracking-[0.1em] text-primary-ink transition-colors hover:bg-transparent hover:text-primary"
                        >
                            {t("land.cta_profile")}
                        </Link>
                        <span className="font-mono text-[10px] text-fg-3">
                            {t("land.cta_or")}{" "}
                            <Link href="/login" className="text-fg-2 transition-colors hover:text-primary">
                                {t("land.signin")}
                            </Link>
                        </span>
                    </div>
                </div>

                <div className="flex flex-col items-start gap-2.5 border-t border-border-2 p-5 md:border-t-0">
                    <span className="font-display text-[8.5px] uppercase tracking-[0.18em] text-primary">
                        {t("land.tier_account")}
                    </span>
                    <p className="max-w-[46ch] font-mono text-[12px] leading-relaxed text-fg-2">
                        <b className="font-normal text-fg">{t("land.conv_lbxd")}</b> {t("land.conv_lbxd_sub")}
                    </p>
                    <div className="flex flex-wrap items-center gap-2.5">
                        <Link
                            href="/register"
                            className="border border-border-2 px-4 py-2.5 font-display text-[10.5px] font-bold uppercase tracking-[0.1em] text-fg-2 transition-colors hover:border-primary hover:text-primary"
                        >
                            {t("land.cta_import")}
                        </Link>
                        <span className="font-mono text-[10px] text-fg-3">{t("land.group")}</span>
                    </div>
                </div>
            </section>

            <footer className="flex shrink-0 flex-wrap items-center gap-4 border-t border-border-2 px-5 py-2.5 font-mono text-[9.5px] text-fg-3">
                <span>{t("land.foot_data")}</span>
                <nav className="ml-auto flex gap-3">
                    <Link href="/privacy" className="transition-colors hover:text-primary">
                        {t("land.privacy")}
                    </Link>
                    <Link href="/terms" className="transition-colors hover:text-primary">
                        {t("land.terms")}
                    </Link>
                </nav>
            </footer>
        </main>
    );
}
