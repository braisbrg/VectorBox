"use client";

// Stats sub-screen — the distributions behind /you's headline numbers.
// ponytail: bars are CSS, no chart dependency. ACID is already rectangles;
// recharts would ship ~90kb to draw a div with a width.

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { getMyStats, StatsResponse } from "@/lib/api";
import { SubScreenHeader } from "@/components/shell/sub-screen-header";
import { useLanguage } from "@/components/language-provider";
import { cn } from "@/lib/utils";

function Panel({ label, note, children }: { label: string; note?: string; children: React.ReactNode }) {
    return (
        <div className="border border-border-2 bg-bg-2">
            <div className="flex items-baseline justify-between gap-3 border-b border-border-2 bg-bg-3 px-3 py-1.5">
                <span className="font-display text-[9px] uppercase tracking-[0.15em] text-fg-3">{label}</span>
                {note && <span className="font-mono text-[9px] text-fg-3">{note}</span>}
            </div>
            <div className="p-4">{children}</div>
        </div>
    );
}

/** Horizontal ranked bars — genres, languages, directors, decades. */
function Bars({ rows }: { rows: { name: string; films: number }[] }) {
    const max = Math.max(...rows.map((r) => r.films), 1);
    return (
        <div className="space-y-1.5">
            {rows.map((r) => (
                <div key={r.name} className="flex items-center gap-2.5 font-mono text-xs text-fg-3">
                    <span className="w-[92px] truncate lowercase" title={r.name}>{r.name}</span>
                    <span className="relative h-[7px] flex-1 border border-border-2 bg-bg-3">
                        <i className="absolute inset-y-0 left-0 bg-primary" style={{ width: `${(r.films / max) * 100}%` }} />
                    </span>
                    <b className="w-8 text-right font-display font-normal text-fg">{r.films}</b>
                </div>
            ))}
        </div>
    );
}

/** Vertical columns — star histogram and films per calendar year. */
function Columns({ rows }: { rows: { label: string; films: number }[] }) {
    const max = Math.max(...rows.map((r) => r.films), 1);
    return (
        <div className="flex h-[150px] items-end gap-[3px]">
            {rows.map((r) => (
                <div key={r.label} className="flex h-full flex-1 flex-col justify-end gap-1" title={`${r.label} · ${r.films}`}>
                    {/* A zero stays empty; anything non-zero keeps a visible stub. */}
                    <span
                        className="w-full bg-primary opacity-80"
                        style={{ height: r.films ? `${Math.max(2, (r.films / max) * 100)}%` : 0 }}
                    />
                    <span className="text-center font-mono text-[8px] text-fg-3">{r.label}</span>
                </div>
            ))}
        </div>
    );
}

/** Ranked film list — the divergence and rewatch panels. */
function FilmRows({ rows }: { rows: { tmdb_id: number; title: string; year?: number | null; value: string }[] }) {
    return (
        <div className="space-y-1">
            {rows.map((f) => (
                <div key={f.tmdb_id} className="flex items-baseline gap-2 font-mono text-[11px]">
                    <span className="flex-1 truncate text-fg-2" title={f.title}>{f.title}</span>
                    {f.year && <span className="text-fg-3">{f.year}</span>}
                    <b className="w-10 shrink-0 text-right font-display font-normal text-primary">{f.value}</b>
                </div>
            ))}
        </div>
    );
}

/** Average ★ per decade. Zero-based on purpose: an axis cropped to the spread
 *  would turn a 0.7★ drift into a cliff. The numbers carry the detail. */
function RatingBars({ rows }: { rows: { decade: number; avg: number; films: number }[] }) {
    return (
        <div className="space-y-1.5">
            {rows.map((r) => (
                <div key={r.decade} className="flex items-center gap-2.5 font-mono text-xs text-fg-3">
                    <span className="w-[52px]">{r.decade}s</span>
                    <span className="relative h-[7px] flex-1 border border-border-2 bg-bg-3">
                        <i className="absolute inset-y-0 left-0 bg-primary" style={{ width: `${(r.avg / 5) * 100}%` }} />
                    </span>
                    <b className="w-8 text-right font-display font-normal text-fg">{r.avg.toFixed(2)}</b>
                </div>
            ))}
        </div>
    );
}

/** Plano de los dos ejes con el centro de masa del gusto. Es lo único que se
 *  puede dibujar honestamente con dos dimensiones ortogonales: un mapa, no
 *  cuatro barras — cuatro barras sugerirían que los cuadrantes suman el total,
 *  y dejan fuera la banda muerta del centro. */
function MoodPlane({ mood, t }: { mood: StatsResponse["mood"]; t: (k: string) => string }) {
    const cells = Object.fromEntries(mood.quadrants.map((q) => [q.name, q.films]));
    const max = Math.max(...mood.quadrants.map((q) => q.films), 1);
    // 0-100 → porcentaje del lado. La humanidad va en X, la gravedad en Y invertida
    // (densa arriba), que es como se leyó siempre en las tablas.
    const x = mood.humanidad ?? 50;
    const y = 100 - (mood.gravedad ?? 50);

    const Cell = ({ q, cls }: { q: string; cls: string }) => (
        <div className={cn("flex flex-col justify-between p-2", cls)}>
            <span className="font-mono text-[9px] uppercase tracking-wide text-fg-3">{t(`mood.${q}`)}</span>
            <span className="font-display text-lg text-fg">{cells[q] ?? 0}</span>
        </div>
    );

    return (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="relative aspect-square w-full max-w-[220px] border border-border-2">
                <div className="grid h-full grid-cols-2 grid-rows-2">
                    {/* opacidad por peso relativo: dónde vive de verdad tu gusto */}
                    <Cell q="dark" cls="border-b border-r border-border-2" />
                    <Cell q="moving" cls="border-b border-border-2" />
                    <Cell q="popcorn" cls="border-r border-border-2" />
                    <Cell q="comforting" cls="" />
                </div>
                {mood.gravedad !== null && (
                    <span
                        className="absolute size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary ring-2 ring-bg"
                        style={{ left: `${x}%`, top: `${y}%` }}
                        title={`${t("stats.mood_centre")}: ${mood.gravedad} / ${mood.humanidad}`}
                    />
                )}
            </div>
            <div className="font-mono text-[11px] text-fg-3">
                <div className="eyebrow mb-1">{t("stats.mood_centre")}</div>
                <p>
                    {t("stats.mood_gravedad")} <b className="font-display font-normal text-primary">{mood.gravedad ?? "—"}</b>
                </p>
                <p>
                    {t("stats.mood_humanidad")} <b className="font-display font-normal text-primary">{mood.humanidad ?? "—"}</b>
                </p>
                <p className="tiny mt-2">
                    {max > 0 ? t("stats.mood_note") : ""}
                </p>
            </div>
        </div>
    );
}

export default function StatsPage() {
    const { t, language } = useLanguage();
    const { data, isLoading } = useQuery<StatsResponse>({
        queryKey: ["stats"],
        queryFn: getMyStats,
        staleTime: 5 * 60 * 1000,
    });

    if (isLoading || !data) {
        return (
            <div className="flex items-center justify-center py-32 font-mono text-xs uppercase tracking-widest text-fg-3">
                {t("stats.loading")}
            </div>
        );
    }

    if (!data.films) {
        return (
            <div className="pt-6">
                <SubScreenHeader crumb="crumbs.stats" />
                <div className="border border-dashed border-border-2 p-8 text-center">
                    <p className="font-display text-3xl text-primary">◫</p>
                    <p className="mt-3 font-display text-lg uppercase text-fg">{t("stats.empty")}</p>
                    <p className="tiny mt-1.5">{t("stats.empty_note")}</p>
                </div>
            </div>
        );
    }

    // ponytail: Intl already knows every weekday name — no 14 new locale keys.
    const dow = new Intl.DateTimeFormat(language, { weekday: "short" });

    const headline: [string, string][] = [
        [`${data.films}`, t("stats.films")],
        [`${data.runtime.total_hours}`, t("stats.hours")],
        [`${data.runtime.avg_minutes}`, t("stats.avg_runtime")],
        [`${data.rated_films}`, t("stats.rated")],
    ];

    return (
        <div className="space-y-5 pt-6">
            <SubScreenHeader crumb="crumbs.stats" />

            <div>
                {/* Desktop back: the other sub-screens sit in the sidebar, so lg
                    users navigate away from them there. /stats does not, which
                    left it with no way out above lg. */}
                <Link
                    href="/you"
                    className="mb-2 hidden items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-fg-3 transition-colors hover:text-primary lg:inline-flex"
                >
                    <span className="font-display text-sm leading-none text-primary">←</span>
                    {t("crumbs.stats")}
                </Link>
                <h1 className="font-display text-2xl uppercase tracking-[-0.02em] text-fg">{t("stats.title")}</h1>
                <p className="tiny mt-1">{t("stats.subtitle")}</p>
            </div>

            <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
                {headline.map(([v, l]) => (
                    <div key={l} className="border border-border p-2.5 font-mono text-[9px] uppercase tracking-[0.06em] text-fg-3">
                        <span className="mb-0.5 block font-display text-2xl normal-case text-primary">{v}</span>
                        {l}
                    </div>
                ))}
            </div>

            {/* Divergence — the one number here you cannot get from Letterboxd. */}
            {data.vs_crowd.delta !== null && (
                <Panel
                    label={t("stats.vs_crowd")}
                    note={`${data.vs_crowd.films}/${data.films} ${t("stats.with_both")}`}
                >
                    <div className="grid grid-cols-1 gap-5 lg:grid-cols-[160px_1fr_1fr]">
                        <div>
                            <span className="block font-display text-3xl text-primary">
                                {data.vs_crowd.delta > 0 ? "+" : ""}{data.vs_crowd.delta}
                            </span>
                            <p className="tiny mt-1">
                                {data.vs_crowd.delta >= 0 ? t("stats.kinder") : t("stats.harsher")}
                            </p>
                        </div>
                        <div>
                            <div className="eyebrow mb-1.5">{t("stats.you_higher")}</div>
                            <FilmRows
                                rows={data.vs_crowd.above.map((f) => ({ ...f, value: `+${f.delta}` }))}
                            />
                        </div>
                        <div>
                            <div className="eyebrow mb-1.5">{t("stats.crowd_higher")}</div>
                            <FilmRows rows={data.vs_crowd.below.map((f) => ({ ...f, value: `${f.delta}` }))} />
                        </div>
                    </div>
                </Panel>
            )}

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <Panel label={t("stats.ratings")} note={`${data.rated_films}/${data.films}`}>
                    <Columns rows={data.rating_histogram.map((r) => ({ label: `${r.stars}`, films: r.films }))} />
                </Panel>

                {/* Two things are missing here, and the note names both: films with
                    no date at all, and films whose only date is a bulk-log day.
                    A short bar means "not measurable", never "a quiet year". */}
                <Panel label={t("stats.per_year")} note={`${data.diary_films}/${data.films} ${t("stats.dated")}`}>
                    {data.per_year.length ? (
                        <Columns rows={data.per_year.map((r) => ({ label: `${r.year}`.slice(2), films: r.films }))} />
                    ) : (
                        <p className="tiny">{t("stats.no_dates")}</p>
                    )}
                </Panel>

                {/* La nota lleva `mood.films`, no `films`: los cuadrantes dejan
                    fuera la banda muerta y las pelis sin ejes. */}
                <Panel label={t("stats.mood")} note={`${data.mood.films}/${data.films}`}>
                    {data.mood.gravedad !== null ? (
                        <MoodPlane mood={data.mood} t={t} />
                    ) : (
                        <p className="tiny">{t("stats.not_enough")}</p>
                    )}
                </Panel>

                <Panel label={t("stats.decades")}>
                    <Bars rows={data.decades.map((d) => ({ name: `${d.decade}s`, films: d.films }))} />
                </Panel>

                <Panel label={t("stats.genres")}>
                    <Bars rows={data.genres} />
                </Panel>

                <Panel label={t("stats.directors")}>
                    <Bars rows={data.directors} />
                </Panel>

                <Panel label={t("stats.actors")} note={t("stats.leads_only")}>
                    <Bars rows={data.actors} />
                </Panel>

                <Panel label={t("stats.languages")}>
                    <Bars rows={data.languages} />
                </Panel>

                <Panel label={t("stats.countries")}>
                    <Bars rows={data.countries} />
                </Panel>

                <Panel label={t("stats.decade_ratings")} note={t("stats.scale_0_5")}>
                    {data.decade_ratings.length ? (
                        <RatingBars rows={data.decade_ratings} />
                    ) : (
                        <p className="tiny">{t("stats.not_enough")}</p>
                    )}
                </Panel>

                <Panel label={t("stats.runtime_bands")}>
                    <Bars rows={data.runtime_bands} />
                </Panel>

                <Panel label={t("stats.obscurity")} note={`${data.obscurity.films}/${data.films}`}>
                    {data.obscurity.median_votes !== null ? (
                        <div className="flex items-baseline gap-6">
                            <div>
                                <span className="block font-display text-2xl text-primary">
                                    {data.obscurity.median_votes.toLocaleString(language)}
                                </span>
                                <p className="tiny mt-1">{t("stats.median_votes")}</p>
                            </div>
                            <div>
                                <span className="block font-display text-2xl text-primary">
                                    {Math.round((data.obscurity.obscure_share ?? 0) * 100)}%
                                </span>
                                <p className="tiny mt-1">{t("stats.obscure_share")}</p>
                            </div>
                        </div>
                    ) : (
                        <p className="tiny">{t("stats.not_enough")}</p>
                    )}
                </Panel>

                <Panel
                    label={t("stats.rewatches")}
                    note={`${data.rewatches.films} · +${data.rewatches.extra_plays}`}
                >
                    {data.rewatches.top.length ? (
                        <FilmRows
                            rows={data.rewatches.top.map((f) => ({ ...f, value: `×${f.plays}` }))}
                        />
                    ) : (
                        <p className="tiny">{t("stats.no_rewatches")}</p>
                    )}
                </Panel>

                {/* Hidden entirely without a diary — seven zeroes would read as a
                    finding ("you never watch on Sundays") instead of no data. */}
                {data.weekday.length > 0 && (
                    <Panel label={t("stats.weekday")} note={`${data.diary_films}/${data.films} ${t("stats.dated")}`}>
                        <Columns
                            rows={data.weekday.map((w) => ({
                                // 2024-01-01 was a Monday, so +day lands on the right name.
                                label: dow.format(new Date(Date.UTC(2024, 0, 1 + w.day))),
                                films: w.films,
                            }))}
                        />
                    </Panel>
                )}
            </div>
        </div>
    );
}
