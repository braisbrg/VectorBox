"use client";

// Magic Box (⌘K) — handoff screens-v3/magic-box.jsx, all three states:
// closed topbar indicator (lives in shell/topbar) · open empty (recents + try)
// · query with results + the PARSED-QUERY STRIP (editable chips → forced_intent
// re-run, no LLM). Used as the ⌘K modal (MagicBoxModal) and inline on /mb.

import { useCallback, useEffect, useRef, useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { useUser } from "@clerk/nextjs";
import { Loader2 } from "lucide-react";
import { api, getTMDBImageUrl } from "@/lib/api";
import { QuickLook, QuickLookFilm } from "@/components/quick-look";
import { useLanguage } from "@/components/language-provider";
import { cn } from "@/lib/utils";

const OPEN_EVENT = "vb:magic-box";
export const openMagicBox = () => window.dispatchEvent(new CustomEvent(OPEN_EVENT));

interface SearchResult {
    movie_id: number;
    title: string;
    overview: string;
    poster_path: string | null;
    score: number;
    year: number;
    runtime?: number;
    genres: string[];
    vectorbox_score?: number;
    title_es?: string;
    overview_es?: string;
    ai_reason?: string;
}

// F10 "find a film" mode — GET /api/search/autocomplete rows (same endpoint as MLT).
interface TitleResult {
    tmdb_id: number;
    title: string;
    year: number | null;
    poster_path: string | null;
}

// Backend MovieSearchIntent fields we surface as chips.
interface SearchIntent {
    semantic_query: string;
    year_min?: number | null;
    year_max?: number | null;
    include_genres?: string[] | null;
    min_runtime_minutes?: number | null;
    max_runtime_minutes?: number | null;
    min_rating?: number | null;
    popularity_vibe?: string | null;
    original_language?: string | null;
    reference_movie?: string | null;
    quality_gate_bypass?: boolean;
    reasoning: string;
    [k: string]: unknown;
}

interface Chip {
    key: string;   // display key (AUTEUR/ERA/…)
    value: string; // display value
    /** intent fields cleared when the chip is removed */
    clears: string[];
}

function intentToChips(intent: SearchIntent): Chip[] {
    const chips: Chip[] = [];
    if (intent.reference_movie) chips.push({ key: "REF", value: intent.reference_movie, clears: ["reference_movie"] });
    if (intent.include_genres?.length)
        chips.push({ key: "GENRE", value: intent.include_genres.join(" · ").toLowerCase(), clears: ["include_genres"] });
    if (intent.year_min || intent.year_max)
        chips.push({
            key: "ERA",
            value: `${intent.year_min ?? "…"}–${intent.year_max ?? "…"}`,
            clears: ["year_min", "year_max"],
        });
    if (intent.max_runtime_minutes || intent.min_runtime_minutes)
        chips.push({
            key: "DURATION",
            value: intent.max_runtime_minutes ? `<${intent.max_runtime_minutes} min` : `>${intent.min_runtime_minutes} min`,
            clears: ["max_runtime_minutes", "min_runtime_minutes"],
        });
    if (intent.min_rating) chips.push({ key: "QUALITY", value: `≥${intent.min_rating}`, clears: ["min_rating"] });
    if (intent.popularity_vibe && intent.popularity_vibe !== "any")
        chips.push({ key: "VIBE", value: intent.popularity_vibe.replace("_", " "), clears: ["popularity_vibe"] });
    if (intent.original_language) chips.push({ key: "LANG", value: intent.original_language, clears: ["original_language"] });
    if (intent.semantic_query) chips.push({ key: "SEMANTIC", value: intent.semantic_query, clears: [] });
    return chips;
}

// F-38: example queries exercising the parser's dimensions (era, awards, country/
// language incl. Spanish input, popularity vibe, runtime, reference film) so users
// discover what the box understands. Literal sample inputs — not i18n keys.
const TRY_SUGGESTIONS = [
    "a film for a rainy sunday",
    "short and weird, like Lanthimos",
    "90s neon noir",
    "oscar-winning thrillers from the 90s",
    "hidden gem korean crime",
    "cine quinqui español",
    "family-friendly animation under 100 min",
    "something like In the Mood for Love but shorter",
];

const RECENTS_KEY = "vb_mb_recents";

function Kbd({ k }: { k: string }) {
    return (
        <span className="border border-border-2 bg-bg-3 px-[5px] py-px font-display text-[9px] tracking-[0.05em] text-fg-2">
            {k}
        </span>
    );
}

// Mirrors TRY_MAX_QUERY_LENGTH in routers/search.py. Truncating here turns a
// 422 into a slightly shorter search, which is the kinder failure.
const GUEST_MAX_QUERY = 140;

export function MagicBox({ embedded = false, onClose }: { embedded?: boolean; onClose?: () => void }) {
    const { isSignedIn } = useUser();
    // The engine reports when the catalogue had nothing close enough to be a
    // recommendation (measured threshold, see magic_search_ranking). An empty
    // shelf plus a reason beats twenty films picked for no reason.
    const [lowConfidence, setLowConfidence] = useState(false);
    // Groq's free tier caps at 8000 tokens per minute and one parse costs ~2000,
    // so a handful of searches in a row leaves the sentence unread. The results
    // are still real films matched on the raw words; what is gone is every
    // constraint the user expressed. Saying so beats quietly serving less.
    const [degraded, setDegraded] = useState(false);
    const { language, t } = useLanguage();
    const router = useRouter();
    const [query, setQuery] = useState("");
    const [results, setResults] = useState<SearchResult[]>([]);
    const [intent, setIntent] = useState<SearchIntent | null>(null);
    const [active, setActive] = useState(0);
    const [elapsed, setElapsed] = useState<number | null>(null);
    const [recents, setRecents] = useState<string[]>([]);
    const [quickLook, setQuickLook] = useState<QuickLookFilm | null>(null);
    // F10 mode toggle: "vibe" = NL semantic search (default) · "title" = literal
    // title lookup → clicking a match opens the film's full page.
    const [mode, setMode] = useState<"vibe" | "title">("vibe");
    const [titleResults, setTitleResults] = useState<TitleResult[]>([]);
    const [titleSearching, setTitleSearching] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        try {
            setRecents(JSON.parse(localStorage.getItem(RECENTS_KEY) || "[]"));
        } catch {}
        inputRef.current?.focus();
    }, []);

    // Title mode: debounced as-you-type autocomplete (same endpoint as the MLT search).
    useEffect(() => {
        if (mode !== "title") return;
        const q = query.trim();
        if (q.length < 2) {
            setTitleResults([]);
            return;
        }
        setTitleSearching(true);
        const h = setTimeout(async () => {
            try {
                const res = await api.get(`/api/search/autocomplete?q=${encodeURIComponent(q)}`);
                setTitleResults(res.data || []);
            } catch {
                setTitleResults([]);
            } finally {
                setTitleSearching(false);
            }
        }, 300);
        return () => clearTimeout(h);
    }, [mode, query]);

    const openTitleResult = (r: TitleResult) => {
        onClose?.();
        router.push(`/movie/${r.tmdb_id}`);
    };

    const searchMutation = useMutation({
        mutationFn: async ({ text, forced }: { text: string; forced?: SearchIntent }) => {
            const t0 = performance.now();
            // Two doors, by session (Fase 3). /natural requires auth and carries
            // the full budget; /try is the bounded public one — 140 chars,
            // 5/minute, no Tier-2 and no forced_intent, which is why the refine
            // path below is only offered to signed-in users.
            const res = isSignedIn
                ? await api.post("/api/search/natural", {
                      query: text,
                      ...(forced ? { forced_intent: forced } : {}),
                      country_code: "ES",
                  })
                : await api.post("/api/search/try", {
                      query: text.slice(0, GUEST_MAX_QUERY),
                      country_code: "ES",
                  });
            return { data: res.data, ms: Math.round(performance.now() - t0) };
        },
        onSuccess: ({ data, ms }) => {
            setLowConfidence(Boolean(data.low_confidence));
            setDegraded(Boolean(data.degraded));
            const unique = Array.from(
                new Map((data.results as SearchResult[]) .map((r) => [r.movie_id, r])).values()
            );
            setResults(unique);
            setIntent(data.intent);
            setActive(0);
            setElapsed(ms);
        },
    });

    const runSearch = useCallback(
        (text: string) => {
            if (!text.trim()) return;
            setQuery(text);
            searchMutation.mutate({ text });
            const next = [text, ...recents.filter((r) => r !== text)].slice(0, 5);
            setRecents(next);
            localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
        },
        [recents, searchMutation]
    );

    // Remove a chip → re-run with forced_intent (bypasses the LLM, keeps edits).
    const removeChip = (chip: Chip) => {
        if (!intent || chip.clears.length === 0) return;
        const forced = { ...intent };
        for (const f of chip.clears) forced[f] = null;
        setIntent(forced);
        searchMutation.mutate({ text: query, forced });
    };

    const openResult = (r: SearchResult) => {
        setQuickLook({
            tmdb_id: r.movie_id,
            title: language === "es" && r.title_es ? r.title_es : r.title,
            year: r.year,
            runtime: r.runtime,
            overview: language === "es" && r.overview_es ? r.overview_es : r.overview,
            poster_url: r.poster_path,
            q: r.vectorbox_score,
            contextLine: r.ai_reason || (r.genres || []).slice(0, 3).join(" · ").toLowerCase() || undefined,
        });
    };

    const onKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => Math.min(a + 1, results.length - 1));
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
        } else if (e.key === "Enter") {
            if (results.length > 0 && document.activeElement === inputRef.current && query.trim() && !searchMutation.isPending && results[active]) {
                // Enter on input with results → open active result only when query already ran
            }
        } else if (e.key === "Escape" && onClose) {
            onClose();
        }
    };

    const hasResults = intent !== null;
    const chips = intent ? intentToChips(intent) : [];
    const scoreOf = (r: SearchResult) => (r.score > 1 ? r.score : r.score * 100);

    return (
        <div
            className={cn("flex w-full flex-col border-2 border-primary bg-bg font-mono", embedded ? "" : "shadow-acid-primary")}
            onKeyDown={onKeyDown}
        >
            {/* query bar */}
            <form
                onSubmit={(e) => {
                    e.preventDefault();
                    if (mode === "title") {
                        if (titleResults[0]) openTitleResult(titleResults[0]);
                        return;
                    }
                    runSearch(query);
                }}
                className="flex items-center gap-2.5 border-b border-border-2 px-4 py-3.5"
            >
                <span className="font-display text-lg text-primary">{mode === "title" ? "⌕" : "◐"}</span>
                <input
                    ref={inputRef}
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder={mode === "title" ? t("mb.title_placeholder") : t("mobile.placeholder")}
                    className="flex-1 bg-transparent font-mono text-[15px] text-fg placeholder:text-fg-3 focus:outline-none"
                />
                {(mode === "title" ? titleSearching : searchMutation.isPending) ? (
                    <Loader2 className="size-4 animate-spin text-primary" />
                ) : mode === "vibe" && elapsed != null ? (
                    <span className="font-display text-[10px] tracking-[0.1em] text-fg-3">{elapsed}ms</span>
                ) : onClose ? (
                    <Kbd k="esc" />
                ) : null}
            </form>

            {/* F10 mode strip — vibe (semantic) ↔ find a film (title → full page) */}
            <div className="flex items-center gap-1.5 border-b border-border-2 px-4 py-2">
                {(["vibe", "title"] as const).map((mo) => (
                    <button
                        key={mo}
                        type="button"
                        onClick={() => { setMode(mo); inputRef.current?.focus(); }}
                        className={cn(
                            "border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide transition-colors",
                            mode === mo
                                ? "border-primary bg-primary font-bold text-primary-ink"
                                : "border-border-2 text-fg-3 hover:border-fg-3"
                        )}
                    >
                        {mo === "vibe" ? t("mb.mode_vibe") : t("mb.mode_title")}
                    </button>
                ))}
            </div>

            {/* F10 title mode — compact result rows → film full page */}
            {mode === "title" && (
                <div className="px-4 py-3.5">
                    {titleResults.length > 0 ? (
                        <div className="flex flex-col">
                            {titleResults.map((r) => (
                                <button
                                    key={r.tmdb_id}
                                    onClick={() => openTitleResult(r)}
                                    className="group flex items-center gap-3 border-b border-border px-1 py-2 text-left transition-colors last:border-b-0 hover:bg-bg-2"
                                >
                                    <span className="relative block h-12 w-8 shrink-0 overflow-hidden border border-border-2 bg-bg-3">
                                        {r.poster_path && (
                                            <Image src={getTMDBImageUrl(r.poster_path, "w92")} alt={r.title} fill sizes="32px" className="object-cover" />
                                        )}
                                    </span>
                                    <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-fg">{r.title}</span>
                                    {r.year && <span className="font-mono text-[11px] text-fg-3">{r.year}</span>}
                                    <span className="font-display text-sm text-fg-3 transition-colors group-hover:text-primary">→</span>
                                </button>
                            ))}
                        </div>
                    ) : (
                        <p className="py-4 text-center font-mono text-[11px] text-fg-3">
                            {query.trim().length >= 2 && !titleSearching ? t("mb.title_none") : t("mb.title_hint")}
                        </p>
                    )}
                </div>
            )}

            {/* STATE 2 — empty: recents + try (vibe mode only) */}
            {mode === "vibe" && !hasResults && !searchMutation.isPending && (
                <div className="px-4 py-3.5">
                    {recents.length > 0 && (
                        <>
                            <div className="eyebrow mb-2">{t("mb.recent")}</div>
                            <div className="mb-3.5 flex flex-col">
                                {recents.map((r, i) => (
                                    <button
                                        key={r}
                                        onClick={() => runSearch(r)}
                                        className={cn(
                                            "flex items-center gap-2 py-1.5 text-left",
                                            i > 0 && "border-t border-dashed border-border-2"
                                        )}
                                    >
                                        <span className="font-display text-[11px] text-fg-3">↻</span>
                                        <span className="flex-1 font-mono text-xs text-fg-2">{r}</span>
                                    </button>
                                ))}
                            </div>
                        </>
                    )}
                    <div className="eyebrow mb-2">{t("mb.try_")}</div>
                    <div className="flex flex-col gap-1.5">
                        {TRY_SUGGESTIONS.map((s) => (
                            <button
                                key={s}
                                onClick={() => runSearch(s)}
                                className="border border-border-2 bg-bg-2 px-3 py-2 text-left font-mono text-xs text-fg transition-colors hover:border-primary"
                            >
                                “{s}”
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {/* STATE 3 — parsed strip + results (vibe mode only) */}
            {mode === "vibe" && hasResults && (
                <>
                    <div className="border-b border-border-2 bg-bg-2 px-4 py-2.5">
                        <div className="eyebrow mb-1.5 text-fg-3">{t("mb.parsed")}</div>
                        <div className="flex flex-wrap gap-1.5">
                            {chips.map((c) => (
                                <span key={c.key + c.value} className="flex items-center border border-border-2 bg-bg-3 font-mono text-[11px]">
                                    <span className="bg-primary px-[7px] py-[3px] font-display text-[9px] font-bold tracking-[0.1em] text-primary-ink">
                                        {c.key}
                                    </span>
                                    <span className="px-[7px] py-[3px] text-fg">{c.value}</span>
                                    {/* Refining a chip re-runs with forced_intent, which the
                                        public /try door rejects by design. Offering the ×
                                        to a guest would silently re-run the whole search and
                                        ignore the edit — worse than not offering it. */}
                                    {c.clears.length > 0 && isSignedIn && (
                                        <button
                                            onClick={() => removeChip(c)}
                                            aria-label={`Remove ${c.key} filter`}
                                            className="border-l border-border-2 px-[7px] py-[3px] text-[9px] text-fg-3 transition-colors hover:text-danger"
                                        >
                                            ×
                                        </button>
                                    )}
                                </span>
                            ))}
                        </div>
                    </div>

                    <div className="max-h-[380px] overflow-y-auto">
                        {degraded && !searchMutation.isPending && (
                            <p className="border-b border-warn/40 bg-warn/10 px-3 py-2 font-mono text-[11px] leading-relaxed text-fg-2">
                                {t("mb.degraded")}
                            </p>
                        )}
                        {results.length === 0 && !searchMutation.isPending && (
                            lowConfidence ? (
                                <div className="p-8 text-center">
                                    <p className="font-mono text-xs uppercase tracking-widest text-fg-2">{t("mb.low_conf")}</p>
                                    <p className="mx-auto mt-2 max-w-[42ch] font-mono text-[11px] leading-relaxed text-fg-3">{t("mb.low_conf_hint")}</p>
                                </div>
                            ) : (
                                <div className="p-8 text-center font-mono text-xs uppercase tracking-widest text-fg-3">{t("mb.no_results")}</div>
                            )
                        )}
                        {results.map((r, i) => (
                            <button
                                key={r.movie_id}
                                onClick={() => openResult(r)}
                                onMouseEnter={() => setActive(i)}
                                className={cn(
                                    "flex w-full items-center gap-3 border-b border-border-2 px-4 py-2.5 text-left transition-colors",
                                    i === active ? "border-l-[3px] border-l-primary bg-bg-2" : "border-l-[3px] border-l-transparent"
                                )}
                            >
                                <span className="w-5 font-display text-[10px] text-fg-3">{String(i + 1).padStart(2, "0")}</span>
                                <div className="poster-art relative h-[54px] w-9 shrink-0 border border-border-2">
                                    {r.poster_path && (
                                        <Image src={getTMDBImageUrl(r.poster_path, "w154")} alt={r.title} fill sizes="36px" className="object-cover" />
                                    )}
                                </div>
                                <div className="min-w-0 flex-1">
                                    <div className="truncate font-mono text-[13px] text-fg">
                                        {language === "es" && r.title_es ? r.title_es : r.title}{" "}
                                        <span className="text-fg-3">
                                            · {r.year}
                                            {r.runtime ? ` · ${r.runtime}m` : ""}
                                        </span>
                                    </div>
                                    <div className="mt-0.5 truncate font-mono text-[10px] text-fg-3">
                                        {r.ai_reason || (r.genres || []).slice(0, 3).join(" · ").toLowerCase()}
                                    </div>
                                </div>
                                <div className="text-right">
                                    <div className="font-display text-[13px] font-bold text-primary">
                                        {r.vectorbox_score != null ? `Q${Math.round(r.vectorbox_score)}` : "—"}
                                    </div>
                                    <div className="font-mono text-[9px] text-fg-3">d {((100 - scoreOf(r)) / 100).toFixed(2)}</div>
                                </div>
                                {i === active && <Kbd k="↵" />}
                            </button>
                        ))}
                    </div>
                </>
            )}

            {/* footer hints */}
            <div className="flex items-center justify-between border-t border-border-2 bg-bg-2 px-4 py-2.5 font-mono text-[10px] text-fg-3">
                {hasResults ? (
                    <span>{results.length} {t("mb.matched")}</span>
                ) : (
                    <div className="flex gap-3.5">
                        <span>
                            <Kbd k="↑↓" /> navigate
                        </span>
                        <span>
                            <Kbd k="↵" /> {t("mb.select")}
                        </span>
                    </div>
                )}
                <span>powered by trident · embeddings only</span>
            </div>

            <QuickLook film={quickLook} context="magic" onClose={() => setQuickLook(null)} />
        </div>
    );
}

/** ⌘K overlay — mounted once in the shell; opens on ⌘K / topbar / openMagicBox(). */
export function MagicBoxModal() {
    const [open, setOpen] = useState(false);

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
                e.preventDefault();
                setOpen((o) => !o);
            }
            if (e.key === "Escape") setOpen(false);
        };
        const onOpen = () => setOpen(true);
        window.addEventListener("keydown", onKey);
        window.addEventListener(OPEN_EVENT, onOpen);
        return () => {
            window.removeEventListener("keydown", onKey);
            window.removeEventListener(OPEN_EVENT, onOpen);
        };
    }, []);

    if (!open) return null;
    return (
        // Full-screen on mobile (handoff: magic covers the whole phone incl. nav); centered overlay ≥lg.
        <div className="fixed inset-0 z-[70] flex items-start justify-center bg-bg p-0 lg:bg-black/60 lg:p-4 lg:pt-[12vh]" onClick={() => setOpen(false)}>
            <div className="h-full w-full overflow-y-auto pt-[max(env(safe-area-inset-top),12px)] lg:h-auto lg:max-w-[680px] lg:pt-0" onClick={(e) => e.stopPropagation()}>
                <MagicBox onClose={() => setOpen(false)} />
            </div>
        </div>
    );
}
