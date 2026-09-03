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
// El modo puede venir en el evento para que se pueda elegir ANTES de abrir: el
// topbar ofrece las dos entradas y quien quiere buscar un título no tiene que
// abrir en "vibe" y cambiar. Sin argumento se respeta el modo recordado.
export const openMagicBox = (mode?: "vibe" | "title") =>
    window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: mode ? { mode } : undefined }));

interface SearchResult {
    movie_id: number;
    title: string;
    overview: string;
    poster_path: string | null;
    // null when no query vector reached the branch that answered (the catalogue
    // selection behind "no sé qué ver" and the quality-only requests). There is
    // no distance to report there, and printing the quality score in its place
    // is what made vague requests read as closer matches than exact ones.
    score: number | null;
    year: number;
    runtime?: number;
    genres: string[];
    vectorbox_score?: number;
    title_es?: string;
    overview_es?: string;
    ai_reason?: string;
    // "era" | "countries" — this film answers the SUBJECT but sits outside a
    // filter the user gave, appended because the exact box came back nearly
    // empty. Never render one of these without saying so.
    outside_filters?: string | null;
}

// F10 "find a film" mode — GET /api/search/autocomplete rows (same endpoint as MLT).
interface TitleResult {
    tmdb_id: number;
    title: string;
    year: number | null;
    poster_path: string | null;
    director: string | null;
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
// El modo elegido se recuerda entre aperturas: alguien que busca por título suele
// volver a buscar por título, y reabrir siempre en "vibe" obliga a un clic cada vez.
const MODE_KEY = "vb_mb_mode";

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

export function MagicBox({ embedded = false, onClose, initialMode }: {
    embedded?: boolean;
    onClose?: () => void;
    initialMode?: "vibe" | "title";
}) {
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
    // Qué filtro se relajó para llenar una fila corta ("era" | "countries").
    // Marcar cada película por separado no explica POR QUÉ hay dos bloques; el
    // usuario ve una lista que de pronto se sale de lo que pidió.
    const [relaxedFilter, setRelaxedFilter] = useState<string | null>(null);
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
    // Sólo con sesión: `/try` es anónimo por diseño y un invitado no tiene lista.
    const [onlyWatchlist, setOnlyWatchlist] = useState(false);
    const [mode, setModeState] = useState<"vibe" | "title">("vibe");
    const setMode = useCallback((m: "vibe" | "title") => {
        setModeState(m);
        try { localStorage.setItem(MODE_KEY, m); } catch {}
    }, []);
    const [titleResults, setTitleResults] = useState<TitleResult[]>([]);
    // El director viaja aparte de las películas: mezclarlos en una sola lista
    // obligaba a inventar cuánto pesa una persona frente a un film.
    const [directorCard, setDirectorCard] = useState<{ name: string; film_count: number; profile_path?: string | null } | null>(null);
    const [titleSearching, setTitleSearching] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        try {
            setRecents(JSON.parse(localStorage.getItem(RECENTS_KEY) || "[]"));
            // Un modo pedido explícitamente al abrir gana al recordado.
            if (initialMode) {
                setModeState(initialMode);
            } else {
                const saved = localStorage.getItem(MODE_KEY);
                if (saved === "title" || saved === "vibe") setModeState(saved);
            }
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
                const payload = res.data;
                // Ver el comentario en more-like-this: forma vieja o nueva.
                setTitleResults(Array.isArray(payload) ? payload : payload?.films || []);
                setActive(0);
                setDirectorCard(Array.isArray(payload) ? null : payload?.director || null);
            } catch {
                setTitleResults([]);
                setDirectorCard(null);
            } finally {
                setTitleSearching(false);
            }
        }, 300);
        return () => clearTimeout(h);
    }, [mode, query]);

    // Toda navegación cierra el modal primero: dejar el desplegable encima de
    // la página recién abierta es la única forma de "llegar" sin haber salido.
    const go = (href: string) => {
        onClose?.();
        router.push(href);
    };
    const openTitleResult = (r: TitleResult) => go(`/movie/${r.tmdb_id}`);

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
                      watchlist: onlyWatchlist,
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
            // El ámbito puede venir de la FRASE ("algo corto de mi lista"), no sólo
            // del botón. Si no se reflejara, el conmutador diría "todo" mientras los
            // resultados ya salen de la lista — y el usuario leería como catálogo
            // entero algo que no lo es.
            if (data.watchlist_applied) setOnlyWatchlist(true);
            setRelaxedFilter(data.relaxed_filter ?? null);
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

    // A nivel de documento, no como onKeyDown del contenedor. Dependía de que el
    // input tuviese el foco para que la tecla burbujease hasta el div; abierto con
    // ⌘K el foco no siempre llegaba, y entonces las flechas se iban al scroll de
    // la página. Sólo funcionaba tras pulsar Tab, que es justo el síntoma.
    useEffect(() => {
        const onDocKey = (e: KeyboardEvent) => {
            if (!["ArrowDown", "ArrowUp", "Enter"].includes(e.key)) return;
            const list = mode === "title" ? titleResults : results;
            if (!list.length) return;
            if (e.key === "ArrowDown") {
                e.preventDefault();
                setActive((a) => Math.min(a + 1, list.length - 1));
            } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setActive((a) => Math.max(a - 1, 0));
            } else if (e.key === "Enter") {
                const target = list[active];
                if (!target) return;
                e.preventDefault();
                if (mode === "title") openTitleResult(target as TitleResult);
                else openResult(target as SearchResult);
            }
        };
        window.addEventListener("keydown", onDocKey);
        return () => window.removeEventListener("keydown", onDocKey);
    }, [mode, titleResults, results, active]);

    const onKeyDown = (e: React.KeyboardEvent) => {
        // La lista navegable depende del modo. Antes esto miraba siempre
        // `results`, así que en modo título las flechas no movían nada — y el pie
        // seguía anunciando "↑↓ navigate".
        const list = mode === "title" ? titleResults : results;
        if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => Math.min(a + 1, list.length - 1));
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
        } else if (e.key === "Enter") {
            // Esta rama estaba VACÍA: solo tenía un comentario describiendo lo que
            // debía hacer. El pie anunciaba "↵ select" y no seleccionaba nada.
            // El form ya maneja Enter cuando no hay fila activa (lanza la
            // búsqueda), así que aquí solo se intercepta si hay algo que abrir.
            const target = list[active];
            if (target) {
                e.preventDefault();
                if (mode === "title") openTitleResult(target as TitleResult);
                else openResult(target as SearchResult);
            }
        } else if (e.key === "Escape" && onClose) {
            onClose();
        }
    };

    const hasResults = intent !== null;
    const chips = intent ? intentToChips(intent) : [];

    return (
        <div
            className={cn("flex w-full flex-col border-2 border-primary bg-bg font-mono", embedded ? "" : "shadow-acid-primary")}
            onKeyDown={onKeyDown}
        >
            {/* Pestañas, no chips: eran dos botoncitos de 10px DEBAJO del input,
                que en móvil ni se veían ni se leían como un conmutador. A todo el
                ancho y arriba del todo, la elección es lo primero que se ve y el
                área táctil deja de ser un problema. */}
            <div className="flex border-b-2 border-border-2">
                {(["vibe", "title"] as const).map((mo) => (
                    <button
                        key={mo}
                        type="button"
                        onClick={() => { setMode(mo); inputRef.current?.focus(); }}
                        aria-pressed={mode === mo}
                        className={cn(
                            "flex flex-1 items-center justify-center gap-2 border-b-2 py-3 font-mono text-[11px] uppercase tracking-[0.1em] transition-colors sm:py-2.5",
                            mode === mo
                                ? "-mb-0.5 border-primary bg-bg-2 font-bold text-primary"
                                : "-mb-0.5 border-transparent text-fg-3 hover:text-fg"
                        )}
                    >
                        <span aria-hidden>{mo === "title" ? "⌕" : "◐"}</span>
                        {mo === "vibe" ? t("mb.mode_vibe") : t("mb.mode_title")}
                    </button>
                ))}
            </div>

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
                {/* Ámbito. Se enciende solo si la frase ya lo pedía, así que el botón
                    y "de mi lista" escrito son la misma cosa vista de dos maneras. */}
                {mode === "vibe" && isSignedIn && (
                    <button
                        type="button"
                        onClick={() => setOnlyWatchlist((v) => !v)}
                        title={t("mb.only_watchlist")}
                        className={cn(
                            "shrink-0 border px-2 py-1 font-display text-[9px] uppercase tracking-[0.1em] transition-colors",
                            onlyWatchlist
                                ? "border-primary bg-primary text-primary-ink"
                                : "border-border-2 text-fg-3 hover:border-fg-3"
                        )}
                    >
                        {t("mb.only_watchlist")}
                    </button>
                )}
                {(mode === "title" ? titleSearching : searchMutation.isPending) ? (
                    <Loader2 className="size-4 animate-spin text-primary" />
                ) : mode === "vibe" && elapsed != null ? (
                    <span className="font-display text-[10px] tracking-[0.1em] text-fg-3">{elapsed}ms</span>
                ) : onClose ? (
                    <Kbd k="esc" />
                ) : null}
            </form>

            {/* F10 mode strip — vibe (semantic) ↔ find a film (title → full page) */}

            {/* F10 title mode — compact result rows → film full page */}
            {mode === "title" && (
                <div className="max-h-[420px] overflow-y-auto px-4 py-3.5">
                    {/* El director va ARRIBA y aparte, no compitiendo en la lista.
                        Así las películas se ordenan sólo por relevancia y no hay
                        que decidir cuánto "pesa" una persona frente a un film. */}
                    {directorCard && (
                        <button
                            onClick={() => go(`/director/${encodeURIComponent(directorCard.name)}`)}
                            className="group mb-2 flex w-full items-center gap-3 border-2 border-primary bg-bg-2 px-3 py-2.5 text-left transition-colors hover:bg-bg-3"
                        >
                            {directorCard.profile_path ? (
                                <span className="relative block size-9 shrink-0 overflow-hidden border border-border-2 bg-bg-3">
                                    <Image
                                        src={getTMDBImageUrl(directorCard.profile_path, "w185") || ""}
                                        alt={directorCard.name}
                                        fill
                                        sizes="36px"
                                        className="object-cover"
                                    />
                                </span>
                            ) : (
                                <span className="flex size-9 shrink-0 items-center justify-center border border-border-2 bg-bg-3 font-display text-[13px] text-fg-3">
                                    {directorCard.name.charAt(0)}
                                </span>
                            )}
                            <span className="font-display text-[10px] uppercase tracking-[0.12em] text-primary">
                                {t("mb.director")}
                            </span>
                            <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-fg">
                                {directorCard.name}
                            </span>
                            <span className="font-mono text-[11px] text-fg-3">
                                {directorCard.film_count} {t("director.count")}
                            </span>
                            <span className="font-display text-sm text-fg-3 transition-colors group-hover:text-primary">→</span>
                        </button>
                    )}
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
                                    <span className="min-w-0 flex-1">
                                        <span className="block truncate font-mono text-[13px] text-fg">{r.title}</span>
                                        {r.director && (
                                            // Un <Link> dentro del <button> de la fila
                                            // sería HTML inválido, así que va como
                                            // span con rol de enlace y su tecla.
                                            // Esto es lo que le quita la presión al
                                            // tope de 12 filas: el desplegable no
                                            // tiene que caber la filmografía entera,
                                            // sólo llevar a ella.
                                            <span
                                                role="link"
                                                tabIndex={0}
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    go(`/director/${encodeURIComponent(r.director!)}`);
                                                }}
                                                onKeyDown={(e) => {
                                                    if (e.key === "Enter" || e.key === " ") {
                                                        e.preventDefault();
                                                        e.stopPropagation();
                                                        go(`/director/${encodeURIComponent(r.director!)}`);
                                                    }
                                                }}
                                                className="block truncate font-mono text-[10px] text-fg-3 hover:text-primary hover:underline focus:text-primary focus:outline-none"
                                            >
                                                {r.director}
                                            </span>
                                        )}
                                    </span>
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
                            <div key={r.movie_id} className="contents">
                            {/* Separador ANTES de la primera película de fuera de
                                los filtros: las relajadas van siempre al final,
                                así que basta comparar con la anterior. */}
                            {r.outside_filters && !results[i - 1]?.outside_filters && (
                                // Franja rellena, no una línea de puntos: bajando
                                // rápido la separación no se veía. `sticky` la
                                // mantiene a la vista mientras se recorre el
                                // segundo bloque, que es cuando hace falta saber
                                // por qué esas películas están ahí.
                                <div className="sticky top-0 z-10 -mx-1 mb-1 mt-3 border-y border-primary bg-bg-3 px-2 py-1.5 font-mono text-[10px] leading-snug text-primary">
                                    {t(`mb.relaxed_${relaxedFilter ?? r.outside_filters}`)}
                                </div>
                            )}
                            <button
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
                                        {r.outside_filters && (
                                            <span className="mr-1.5 text-primary">
                                                {t(`mb.outside_${r.outside_filters}`)}
                                            </span>
                                        )}
                                        {r.ai_reason || (r.genres || []).slice(0, 3).join(" · ").toLowerCase()}
                                    </div>
                                </div>
                                <div className="text-right">
                                    <div className="font-display text-[13px] font-bold text-primary">
                                        {r.vectorbox_score != null ? `Q${Math.round(r.vectorbox_score)}` : "—"}
                                    </div>
                                    {/* The per-film distance used to live here. The
                                        row is ordered by relevance AND quality, so a
                                        column showing only relevance read as a broken
                                        ranking — The Man Who Would Be King sat 2nd at
                                        69 above a film 8th at 78. Q below is the half
                                        a viewer can act on; the other half is the
                                        order itself. */}
                                </div>
                                {i === active && <Kbd k="↵" />}
                            </button>
                            </div>
                        ))}
                    </div>
                </>
            )}

            {/* footer hints */}
            <div className="flex items-center justify-between border-t border-border-2 bg-bg-2 px-4 py-2.5 font-mono text-[10px] text-fg-3">
                {/* El hint salía cuando NO había resultados y desaparecía al
                    haberlos: se anunciaba justo cuando no había nada que navegar.
                    Ahora acompaña a la lista, en los dos modos. */}
                {(mode === "title" ? titleResults.length > 0 : hasResults) ? (
                    <div className="flex items-center gap-3.5">
                        <span>
                            {mode === "title" ? titleResults.length : results.length}{" "}
                            {t("mb.matched")}
                        </span>
                        <span>
                            <Kbd k="↑↓" /> {t("mb.navigate")}
                        </span>
                        <span>
                            <Kbd k="↵" /> {t("mb.select")}
                        </span>
                    </div>
                ) : (
                    <div className="flex gap-3.5">
                        <span>
                            <Kbd k="↑↓" /> {t("mb.navigate")}
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
    const [requestedMode, setRequestedMode] = useState<"vibe" | "title" | undefined>();

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
                e.preventDefault();
                setOpen((o) => !o);
            }
            if (e.key === "Escape") setOpen(false);
        };
        const onOpen = (e: Event) => {
            setRequestedMode((e as CustomEvent).detail?.mode);
            setOpen(true);
        };
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
                <MagicBox onClose={() => setOpen(false)} initialMode={requestedMode} />
            </div>
        </div>
    );
}
