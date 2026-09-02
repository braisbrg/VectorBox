"use client";

// Settings — handoff screens-v3/settings-full.jsx structure: sub-page tab rail
// with app-wide shared state. Providers/country live in the persistent right
// rail (per provider-rail.jsx), so the tabs here are: account · import · taste
// · appearance · privacy · advanced. All pre-migration functionality preserved:
// RSS sync, web-watches CSV export (F-22), ZIP re-upload, rejected-list undo,
// content-preference tags, about, legal.

import Link from "next/link";
import Image from "next/image";
import { useState, useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, Undo2 } from "lucide-react";
import { useLanguage } from "@/components/language-provider";
import { LanguageToggle } from "@/components/language-toggle";
import { useSettings } from "@/lib/hooks";
import { syncRSS, VectorboxUser, getRejectedMovies, unrejectMovie, getTMDBImageUrl, USER_SESSION_KEY, api } from "@/lib/api";
import { UploadZone } from "@/components/upload-zone";
import { WebWatchesPanel } from "@/components/web-watches-panel";
import {
    TagSelector,
    TagState,
    tagStateToPreferences,
    preferencesToTagState,
} from "@/components/onboarding/tag-selector";
import { useVectorboxLogout } from "@/hooks/useVectorboxLogout";
import { useShell } from "@/components/shell/shell-context";
import { BracketToggle } from "@/components/ui/bracket-toggle";
import { COUNTRIES, getProvidersForCountry } from "@/lib/constants";
import { HEX_RE, applyCustomAccent, resolveAccent } from "@/lib/accent";
import { AccentPicker } from "@/components/ui/accent-picker";
import { cn } from "@/lib/utils";

type Tab = "account" | "import" | "providers" | "taste" | "appearance" | "privacy" | "advanced";

const TABS: { k: Tab; key: string }[] = [
    { k: "account", key: "set.account" },
    { k: "import", key: "set.import" },
    { k: "providers", key: "set.providers" },
    { k: "taste", key: "set.taste" },
    { k: "appearance", key: "set.appearance" },
    { k: "privacy", key: "set.privacy" },
    { k: "advanced", key: "set.advanced" },
];

// The 6 locked accent palettes (kit-acid.css). The swatch uses the SAME oklch
// value each theme sets on --primary, so the browser clamps it to the exact
// colour the live app renders (e.g. acid → #f7e800, not a stale hex).
const THEMES = [
    { k: "acid", primary: "oklch(0.9 0.4 110)" },
    { k: "cyan", primary: "oklch(0.85 0.18 200)" },
    { k: "magenta", primary: "oklch(0.7 0.3 0)" },
    { k: "orange", primary: "oklch(0.83 0.16 82)" },
    { k: "bone", primary: "oklch(0.92 0.02 80)" },
    { k: "blood", primary: "oklch(0.55 0.25 25)" },
];

const THEME_KEY = "vb_theme";
const ACCENT_KEY = "vb_accent";

function Section({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
    return (
        <div className="border border-border-2 bg-bg-2 p-4">
            <h3 className="eyebrow mb-1 text-primary">{title}</h3>
            {note && <p className="mb-3 font-mono text-[11px] leading-relaxed text-fg-3">{note}</p>}
            {children}
        </div>
    );
}

export function SettingsView() {
    const { t } = useLanguage();
    const { mounted } = useSettings();
    const [tab, setTab] = useState<Tab>("account");
    const [letterboxdUsername, setLetterboxdUsername] = useState<string | null>(null);
    const [currentUser, setCurrentUser] = useState<VectorboxUser | null>(null);
    const [syncMessage, setSyncMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
    const [showReupload, setShowReupload] = useState(false);
    const [theme, setThemeState] = useState("acid");
    const [custom, setCustom] = useState<string | null>(null);
    const [draft, setDraft] = useState(""); // empty until the user picks their own — the chip must not echo the active preset
    const [seed, setSeed] = useState("#f7e800"); // where the colour well opens from while no custom is set
    const queryClient = useQueryClient();
    const handleLogout = useVectorboxLogout();

    useEffect(() => {
        try {
            const user = JSON.parse(localStorage.getItem(USER_SESSION_KEY) || "{}");
            setLetterboxdUsername(user?.letterboxd_username ?? null);
            if (user?.id) {
                setCurrentUser({
                    id: Number(user.id),
                    username: user.username ?? "",
                    has_data: user.has_data ?? true,
                    letterboxd_username: user.letterboxd_username ?? null,
                });
            }
        } catch {
            setLetterboxdUsername(null);
        }
        setThemeState(localStorage.getItem(THEME_KEY) || "acid");
        const saved = localStorage.getItem(ACCENT_KEY);
        const valid = saved && HEX_RE.test(saved) ? saved : null;
        setCustom(valid);
        setDraft(valid ?? "");
        setSeed(resolveAccent()); // layout.tsx already applied theme + custom pre-paint
    }, []);

    const setTheme = (k: string) => {
        setThemeState(k);
        localStorage.setItem(THEME_KEY, k);
        document.documentElement.dataset.theme = k;
        // a preset drops the custom accent — otherwise the inline --primary keeps winning
        clearAccent();
    };

    const clearAccent = () => {
        localStorage.removeItem(ACCENT_KEY);
        applyCustomAccent(null);
        setCustom(null);
        setDraft("");
        setSeed(resolveAccent());
    };

    // The colour well and the hex field share one draft; only a complete #rrggbb
    // applies, so half-typed values neither paint nor persist. Emptying the field
    // hands the accent back to the selected preset.
    const setAccent = (raw: string) => {
        const v = raw.startsWith("#") ? raw : `#${raw}`;
        if (v === "#") return clearAccent();
        setDraft(v);
        if (!HEX_RE.test(v)) return;
        localStorage.setItem(ACCENT_KEY, v);
        applyCustomAccent(v);
        setCustom(v);
    };

    const syncMutation = useMutation({
        mutationFn: (username: string) => syncRSS(username),
        onSuccess: () => setSyncMessage({ type: "success", text: "Sync started — your feed will update shortly." }),
        onError: (error: Error) => setSyncMessage({ type: "error", text: error.message || "Sync failed. Please try again." }),
    });

    const { data: rejectedMovies, isLoading: rejectedLoading } = useQuery({
        queryKey: ["rejected-movies"],
        queryFn: getRejectedMovies,
        staleTime: 60_000,
    });

    const unrejectMutation = useMutation({
        mutationFn: (tmdbId: number) => unrejectMovie(tmdbId),
        onMutate: async (tmdbId) => {
            await queryClient.cancelQueries({ queryKey: ["rejected-movies"] });
            const previous = queryClient.getQueryData(["rejected-movies"]);
            queryClient.setQueryData(["rejected-movies"], (old: typeof rejectedMovies) =>
                old?.filter((m) => m.tmdb_id !== tmdbId)
            );
            return { previous };
        },
        onError: (_err, _tmdbId, context) => {
            queryClient.setQueryData(["rejected-movies"], context?.previous);
        },
        onSettled: () => {
            queryClient.invalidateQueries({ queryKey: ["rejected-movies"] });
            queryClient.invalidateQueries({ queryKey: ["feed"] });
        },
    });

    if (!mounted) return null;

    return (
        <div className="pt-6">
            <h1 className="mb-1 font-display text-2xl uppercase tracking-[-0.02em] text-fg">{t("settings.title")}</h1>
            <p className="tiny mb-5">{t("settings.subtitle")}</p>

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-[170px_1fr]">
                {/* tab rail */}
                <nav className="flex gap-0 overflow-x-auto border border-border-2 scrollbar-hide lg:flex-col lg:self-start">
                    {TABS.map((tb) => (
                        <button
                            key={tb.k}
                            onClick={() => setTab(tb.k)}
                            className={cn(
                                "shrink-0 border-r border-border-2 px-4 py-2.5 text-left font-mono text-[11px] uppercase tracking-[0.08em] transition-colors last:border-r-0 lg:border-b lg:border-r-0 lg:last:border-b-0",
                                tab === tb.k ? "bg-primary font-bold text-primary-ink" : "text-fg-2 hover:bg-bg-2 hover:text-fg"
                            )}
                        >
                            {t(tb.key)}
                        </button>
                    ))}
                </nav>

                {/* panel */}
                <div className="min-w-0 max-w-2xl space-y-4">
                    {tab === "account" && (
                        <>
                            <Section title={t("setb.identity")}>
                                <div className="space-y-1 font-mono text-xs text-fg-2">
                                    <div>
                                        username · <span className="text-fg">{currentUser?.username ?? "—"}</span>
                                    </div>
                                    <div>
                                        letterboxd ·{" "}
                                        <span className="text-fg">{letterboxdUsername ? `@${letterboxdUsername}` : "not linked"}</span>
                                    </div>
                                </div>
                            </Section>
                            <Section title={t("setb.session")}>
                                <button
                                    onClick={handleLogout}
                                    className="border border-danger/60 px-4 py-2 font-mono text-[11px] uppercase tracking-[0.08em] text-danger transition-colors hover:bg-danger hover:text-bg"
                                >
                                    log out
                                </button>
                            </Section>
                        </>
                    )}

                    {tab === "import" && (
                        <>
                            {letterboxdUsername && (
                                <Section
                                    title={t("setb.lb_sync")}
                                    note={`${t("setb.lb_sync_note_1")} @${letterboxdUsername}.`}
                                >
                                    <button
                                        onClick={() => syncMutation.mutate(letterboxdUsername)}
                                        disabled={syncMutation.isPending}
                                        className="flex items-center gap-2 border border-primary px-4 py-2 font-mono text-[11px] uppercase tracking-[0.05em] text-primary transition-colors hover:bg-primary hover:text-primary-ink disabled:cursor-not-allowed disabled:opacity-50"
                                    >
                                        {syncMutation.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
                                        sync now
                                    </button>
                                    {syncMessage && (
                                        <p className={cn("mt-2 font-mono text-[11px]", syncMessage.type === "success" ? "text-primary" : "text-danger")}>
                                            {syncMessage.text}
                                        </p>
                                    )}
                                </Section>
                            )}

                            {/* F-22: web watches → Letterboxd CSV export — its designed home */}
                            <WebWatchesPanel />

                            {currentUser && (
                                <Section
                                    title={t("setb.lb_data")}
                                    note={t("setb.lb_data_note")}
                                >
                                    {!showReupload ? (
                                        <div className="flex flex-wrap gap-2">
                                            <button
                                                onClick={() => setShowReupload(true)}
                                                className="border border-border-2 px-3 py-1.5 font-mono text-[11px] uppercase text-fg-2 transition-colors hover:border-primary hover:text-primary"
                                            >
                                                [ re-upload export ]
                                            </button>
                                            {/* Antes decía "open import screen →" y hacía LO MISMO
                                                que el botón de al lado, sólo que llevándote al
                                                asistente de 5 pasos del onboarding — parecías estar
                                                a medio registro. Ahora cada puerta tiene su trabajo:
                                                aquí se re-sube el ZIP, y el asistente queda para lo
                                                único que la subida en línea no hace, re-vincular la
                                                cuenta. Entra por ?step=identify, que ya estaba
                                                soportado y no lo usaba nadie. */}
                                            <Link
                                                href="/import?step=identify"
                                                className="border border-border-2 px-3 py-1.5 font-mono text-[11px] uppercase text-fg-2 transition-colors hover:border-primary hover:text-primary"
                                            >
                                                {t("setb.relink")}
                                            </Link>
                                        </div>
                                    ) : (
                                        <div className="space-y-3">
                                            <button
                                                onClick={() => setShowReupload(false)}
                                                className="font-mono text-[11px] uppercase text-fg-3 transition-colors hover:text-fg"
                                            >
                                                [ cancel ]
                                            </button>
                                            <UploadZone
                                                onUploadSuccess={() => {
                                                    setShowReupload(false);
                                                    queryClient.invalidateQueries({ queryKey: ["feed"] });
                                                }}
                                                registeredUsers={[currentUser]}
                                                onUserCreated={() => {}}
                                                activeSessionUserId={currentUser.id}
                                            />
                                        </div>
                                    )}
                                </Section>
                            )}
                        </>
                    )}

                    {tab === "providers" && <ProvidersSection />}

                    {tab === "taste" && (
                        <>
                            <ContentPreferencesSection />
                            <ShortFilmsSection />
                            <Section
                                title={t("setb.not_interested")}
                                note={t("setb.not_interested_note")}
                            >
                                {rejectedLoading ? (
                                    <div className="flex items-center gap-2 font-mono text-xs text-fg-3">
                                        <Loader2 className="size-3 animate-spin" /> loading…
                                    </div>
                                ) : !rejectedMovies || rejectedMovies.length === 0 ? (
                                    <p className="font-mono text-xs text-fg-3">no rejected films yet.</p>
                                ) : (
                                    <div className="max-h-[320px] space-y-1.5 overflow-y-auto scrollbar-hide">
                                        {rejectedMovies.map((movie) => (
                                            <div
                                                key={movie.tmdb_id}
                                                className="group flex items-center gap-3 border border-border p-2 transition-colors hover:border-border-2"
                                            >
                                                <div className="h-12 w-8 shrink-0 overflow-hidden bg-bg-3">
                                                    {movie.poster_path && (
                                                        <Image
                                                            src={getTMDBImageUrl(movie.poster_path, "w92")}
                                                            alt={movie.title}
                                                            width={32}
                                                            height={48}
                                                            className="size-full object-cover"
                                                        />
                                                    )}
                                                </div>
                                                <div className="min-w-0 flex-1">
                                                    <p className="truncate font-mono text-xs text-fg">{movie.title}</p>
                                                    {movie.year && <p className="font-mono text-[10px] text-fg-3">{movie.year}</p>}
                                                </div>
                                                <button
                                                    onClick={() => unrejectMutation.mutate(movie.tmdb_id)}
                                                    disabled={unrejectMutation.isPending}
                                                    className="flex items-center gap-1 border border-transparent px-2 py-1 font-mono text-[10px] uppercase text-fg-3 opacity-0 transition-[opacity,color,border-color] focus:opacity-100 group-hover:opacity-100 hover:border-primary hover:text-primary"
                                                    title={t("setb.undo_rejection")}
                                                >
                                                    <Undo2 className="size-3" /> undo
                                                </button>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </Section>
                        </>
                    )}

                    {tab === "appearance" && (
                        <>
                            <Section title={t("setb.theme")} note={t("setb.theme_note")}>
                                <div className="flex flex-wrap gap-2">
                                    {THEMES.map((th) => (
                                        <button
                                            key={th.k}
                                            onClick={() => setTheme(th.k)}
                                            className={cn(
                                                "flex items-center gap-2 border px-3 py-2 font-mono text-[11px] lowercase transition-colors",
                                                !custom && theme === th.k ? "border-primary text-fg" : "border-border-2 text-fg-2 hover:border-fg-3"
                                            )}
                                        >
                                            <span className="size-3.5 border border-black/40" style={{ background: th.primary }} />
                                            {th.k}
                                            {!custom && theme === th.k && <span className="text-primary">●</span>}
                                        </button>
                                    ))}

                                    {/* seventh chip: the presets are the base, this picks anything else.
                                        Dashed while unset and labelled "custom" — echoing the active
                                        preset's hex here made it read as a duplicate swatch. */}
                                    <div
                                        className={cn(
                                            "flex items-center gap-2 border px-3 py-2 font-mono text-[11px] lowercase transition-colors",
                                            custom ? "border-primary text-fg" : "border-dashed border-border-2 text-fg-3 hover:border-fg-3"
                                        )}
                                    >
                                        <AccentPicker
                                            label={t("setb.custom_accent")}
                                            value={HEX_RE.test(draft) ? draft : seed}
                                            onChange={setAccent}
                                            className={cn(
                                                "size-3.5 cursor-pointer border",
                                                custom ? "border-black/40" : "border-dashed border-fg-3 opacity-60"
                                            )}
                                        />
                                        <input
                                            type="text"
                                            value={draft}
                                            onChange={(e) => setAccent(e.target.value)}
                                            spellCheck={false}
                                            maxLength={7}
                                            placeholder={t("setb.custom_accent_short")}
                                            className="w-[56px] bg-transparent text-inherit outline-none placeholder:text-fg-3"
                                        />
                                        {custom && <span className="text-primary">●</span>}
                                    </div>
                                </div>
                            </Section>
                            <Section title={t("setb.language")}>
                                <LanguageToggle isCollapsed={false} />
                            </Section>
                        </>
                    )}

                    {tab === "privacy" && (
                        <Section
                            title={t("setb.your_data")}
                            note={t("setb.your_data_note")}
                        >
                            <div className="flex gap-3 font-mono text-[11px] uppercase tracking-[0.08em]">
                                <Link href="/privacy" className="text-fg-2 underline underline-offset-4 hover:text-primary">
                                    privacy policy
                                </Link>
                                <span className="text-border-2">·</span>
                                <Link href="/terms" className="text-fg-2 underline underline-offset-4 hover:text-primary">
                                    terms of service
                                </Link>
                            </div>
                        </Section>
                    )}

                    {tab === "advanced" && (
                        <Section title={t("settings.about.title")} note={t("settings.about.desc")}>
                            <p className="font-mono text-[11px] text-fg-3">{t("settings.about.version")}</p>
                            <div className="mt-4 space-y-2 border-t border-border-2 pt-4 font-mono text-[10px] text-fg-3">
                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                <img src="/tmdb.svg" alt="TMDB" className="h-2.5" />
                                <p>{t("settings.about.credits_tmdb")}</p>
                                <p>{t("settings.about.credits_justwatch")}</p>
                                <p>
                                    {t("settings.about.credits_omdb")}{" "}
                                    <a href="https://creativecommons.org/licenses/by-nc/4.0/" target="_blank" rel="noreferrer" className="underline underline-offset-2 hover:text-primary">CC BY-NC 4.0</a>
                                </p>
                                <p>{t("settings.about.credits_letterboxd")}</p>
                            </div>
                        </Section>
                    )}
                </div>
            </div>
        </div>
    );
}

/**
 * PROVIDERS — handoff settings-full.jsx Settings_Providers: region selector +
 * provider toggle cards (active = lime left edge). SAME state as the right
 * rail via useShell (single source of truth — prototype cross-reference note).
 * Per-provider film counts omitted: no backend aggregate (documented deviation).
 */
function ProvidersSection() {
    const { t } = useLanguage();
    const { countryCode, streamingProviders, setCountryCode, toggleProvider } = useShell();
    const providers = getProvidersForCountry(countryCode);
    const activeCount = streamingProviders.length;

    return (
        <>
            <Section title={t("setb.region")} note={t("setb.region_note")}>
                <div className="flex flex-wrap gap-1">
                    {COUNTRIES.map((c) => (
                        <button
                            key={c.code}
                            onClick={() => setCountryCode(c.code)}
                            className={cn(
                                "border px-2.5 py-1.5 font-mono text-[11px] uppercase tracking-wide transition-colors",
                                countryCode === c.code
                                    ? "border-primary bg-primary font-bold text-primary-ink"
                                    : "border-border-2 text-fg-3 hover:border-fg-3"
                            )}
                        >
                            {c.code}
                        </button>
                    ))}
                </div>
            </Section>
            <Section
                title={t("setb.active_providers")}
                note={t("setb.active_providers_note")}
            >
                <div className="grid max-w-[560px] grid-cols-1 gap-2 sm:grid-cols-2">
                    {providers.map((p) => {
                        const on = streamingProviders.includes(p.id);
                        return (
                            <div
                                key={p.id}
                                className={cn(
                                    "flex items-center gap-2.5 border border-border-2 px-3 py-2.5",
                                    on ? "border-l-[3px] border-l-primary bg-bg-2" : "bg-transparent"
                                )}
                            >
                                <BracketToggle checked={on} onChange={() => toggleProvider(p.id)} label={p.name} />
                                <div className="min-w-0 flex-1">
                                    <div className={cn("font-mono text-xs lowercase", on ? "text-fg" : "text-fg-3")}>{p.name}</div>
                                    <div className="font-mono text-[10px] text-fg-3">{countryCode}</div>
                                </div>
                            </div>
                        );
                    })}
                </div>
                <p className="mt-3 font-mono text-[11px] text-fg-3">
                    {activeCount === 0 ? "no filter — all providers shown" : `${activeCount} active · feed hard-filtered to these`}
                </p>
            </Section>
        </>
    );
}

/**
 * SHORT FILMS — el feed omite los cortos (<= 40 min) salvo que se active aquí.
 * El PATCH invalida el feed cacheado en el backend, así que el cambio se ve en
 * la siguiente carga sin esperar al TTL de la fila.
 */
function ShortFilmsSection() {
    const { t } = useLanguage();
    const [on, setOn] = useState<boolean | null>(null);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        api.get("/api/users")
            .then((res) => setOn(Boolean(res.data?.[0]?.include_shorts)))
            .catch(() => setOn(false));
    }, []);

    const toggle = async (next: boolean) => {
        const previous = on;
        setOn(next);          // optimista
        setSaving(true);
        try {
            await api.patch("/api/users/me/preferences", { include_shorts: next });
        } catch (e) {
            console.error("Failed to save short-film preference:", e);
            setOn(previous);  // revertir: el feed no cambió, el interruptor tampoco debe mentir
        } finally {
            setSaving(false);
        }
    };

    if (on === null) return null;

    return (
        <Section title={t("setb.shorts")} note={t("setb.shorts_note")}>
            <div className="flex items-center justify-between gap-4">
                <span className="font-mono text-xs text-fg-2">{t("setb.shorts_label")}</span>
                <BracketToggle
                    checked={on}
                    onChange={toggle}
                    disabled={saving}
                    label={t("setb.shorts_label")}
                />
            </div>
        </Section>
    );
}

/**
 * CONTENT PREFERENCES — fetches from /onboarding/status, saves via /onboarding/tags.
 */
function ContentPreferencesSection() {
    const { t } = useLanguage();
    const [tagStates, setTagStates] = useState<Record<string, TagState>>({});
    const [loaded, setLoaded] = useState(false);
    const [saving, setSaving] = useState(false);
    const saveTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        api.get("/api/onboarding/status")
            .then((res) => {
                const prefs = res.data?.tag_preferences;
                setTagStates(preferencesToTagState(prefs || null));
            })
            .catch(() => {})
            .finally(() => setLoaded(true));
    }, []);

    const handleChange = (next: Record<string, TagState>) => {
        setTagStates(next);
        if (saveTimeout.current) clearTimeout(saveTimeout.current);
        saveTimeout.current = setTimeout(async () => {
            setSaving(true);
            try {
                await api.post("/api/onboarding/tags", tagStateToPreferences(next));
            } catch (e) {
                console.error("Failed to save tag preferences:", e);
            } finally {
                setSaving(false);
            }
        }, 600);
    };

    if (!loaded) return null;

    return (
        <Section
            title={t("setb.content_prefs")}
            note={t("setb.content_prefs_note")}
        >
            {saving && <span className="mb-2 block animate-pulse font-mono text-[10px] text-fg-3">saving…</span>}
            <TagSelector value={tagStates} onChange={handleChange} compact />
        </Section>
    );
}
