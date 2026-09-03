"use client";

// Ficha de director: nombre, cuántas películas y la rejilla completa.
//
// ponytail: sin biografía ni foto — eso es el endpoint /person de TMDB, otra
// llamada y otra caché. Y sin paginar en la UI: el backend acepta limit/offset,
// pero el director con más obra del catálogo no llega a 50, así que un botón
// "cargar más" sería código muerto desde el día uno. Se añade cuando un director
// pase de PAGE_MAX de verdad.

import { useState, use } from "react";
import Image from "next/image";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useLanguage } from "@/components/language-provider";

import { getDirector, getTMDBImageUrl, DirectorSort } from "@/lib/api";

export default function DirectorPage({ params }: { params: Promise<{ name: string }> }) {
    const { name } = use(params);
    const decoded = decodeURIComponent(name);
    const { t, language } = useLanguage();
    const [sort, setSort] = useState<DirectorSort>("popularity");

    const { data, isLoading, isError } = useQuery({
        // The bio is language-dependent, so the locale belongs in the key —
        // otherwise switching language serves the cached English page.
        queryKey: ["director", decoded, sort, language],
        queryFn: () => getDirector(decoded, sort, language),
    });

    if (isLoading) {
        return (
            <div className="flex min-h-[60vh] items-center justify-center">
                <Loader2 className="size-6 animate-spin text-primary" />
            </div>
        );
    }

    if (isError || !data) {
        return (
            <div className="flex min-h-[60vh] flex-col items-center justify-center gap-2 font-mono text-sm text-fg-3">
                <span className="font-display text-lg text-fg">{decoded}</span>
                <span>{t("director.empty")}</span>
            </div>
        );
    }

    return (
        <div className="mx-auto w-full max-w-6xl px-4 py-8">
            <header className="mb-6 flex flex-col gap-4 border-b-2 border-primary pb-5 sm:flex-row sm:items-start">
                {data.profile_path && (
                    <div className="relative h-40 w-28 shrink-0 overflow-hidden border border-border-2 bg-bg-2">
                        <Image
                            src={getTMDBImageUrl(data.profile_path, "w185") || ""}
                            alt={data.name}
                            fill
                            sizes="112px"
                            className="object-cover"
                        />
                    </div>
                )}
                <div className="min-w-0 flex-1">
                    <h1 className="font-display text-3xl text-fg">{data.name}</h1>
                    <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.1em] text-fg-3">
                        {`${data.total} ${t("director.count")}`}
                        {data.birthday ? ` · ${t("director.born")} ${data.birthday}` : ""}
                        {data.place_of_birth ? ` · ${data.place_of_birth}` : ""}
                    </p>
                    {data.biography && (
                        // line-clamp en vez de un "leer más": una biografía de TMDB
                        // ronda los 2000 caracteres y aquí es contexto, no el
                        // contenido. Si alguien pide leerla entera, se añade.
                        <p className="mt-2 line-clamp-4 font-mono text-[12px] leading-relaxed text-fg-2">
                            {data.biography}
                        </p>
                    )}
                </div>
            </header>

            {/* Yours — absent when signed out. Accented because it is the only
                block on the page that is about the reader, not the director. */}
            {data.library && (
                <div className="mb-4 flex flex-col gap-4 border border-primary bg-bg-2 p-3.5 sm:flex-row sm:items-center">
                    <div className="flex items-baseline gap-1.5">
                        <span className="font-display text-2xl text-primary">{data.library.seen}</span>
                        <span className="font-mono text-[11px] text-fg-3">/ {data.total} {t("dir.seen_of")}</span>
                    </div>

                    {/* Withheld under the floor — see MIN_FILMS_TO_COMPARE. */}
                    {data.library.vs_your_avg !== null && (
                        <div className="font-mono text-[11px] text-fg-2">
                            <b className="font-display text-base font-normal text-primary">
                                {data.library.vs_your_avg > 0 ? "+" : ""}{data.library.vs_your_avg}★
                            </b>{" "}
                            {data.library.vs_your_avg >= 0 ? t("dir.above_you") : t("dir.below_you")}
                        </div>
                    )}

                    {data.library.watchlisted > 0 && (
                        <div className="font-mono text-[11px] text-fg-3">
                            {data.library.watchlisted} {t("dir.in_watchlist")}
                        </div>
                    )}

                    {data.library.best_unseen && (
                        <Link
                            href={`/movie/${data.library.best_unseen.tmdb_id}`}
                            className="group ml-auto flex items-baseline gap-2 font-mono text-[11px] text-fg-3 transition-colors hover:text-primary"
                        >
                            <span className="eyebrow">{t("dir.next_up")}</span>
                            <b className="font-display font-normal text-fg group-hover:text-primary">
                                {data.library.best_unseen.title}
                            </b>
                            {data.library.best_unseen.year && <span>{data.library.best_unseen.year}</span>}
                        </Link>
                    )}
                </div>
            )}

            {/* Shape of the body of work — computed over the whole filmography,
                not the page being shown, so sorting never changes it. */}
            <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
                <div className="border border-border-2 bg-bg-2 p-3.5">
                    <div className="eyebrow mb-2">{t("dir.also_with")}</div>
                    {data.recurring_cast.length ? (
                        <div className="space-y-1">
                            {data.recurring_cast.slice(0, 5).map((a) => (
                                <div key={a.name} className="flex items-baseline gap-2 font-mono text-[11px]">
                                    <span className="flex-1 truncate text-fg-2">{a.name}</span>
                                    <b className="font-display font-normal text-primary">{a.films}</b>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <p className="tiny">—</p>
                    )}
                </div>

                <div className="border border-border-2 bg-bg-2 p-3.5">
                    <div className="eyebrow mb-2">{t("dir.signature")}</div>
                    <div className="flex flex-wrap gap-1.5">
                        {data.genres.map((g) => (
                            <span key={g.name} className="border border-border-2 px-1.5 py-0.5 font-mono text-[10px] text-fg-2">
                                {g.name} <b className="font-display font-normal text-primary">{g.films}</b>
                            </span>
                        ))}
                    </div>
                </div>

                <div className="border border-border-2 bg-bg-2 p-3.5">
                    <div className="eyebrow mb-2">{t("dir.span")}</div>
                    <span className="block font-display text-xl text-primary">
                        {data.span ? `${data.span.from}–${data.span.to}` : "—"}
                    </span>
                    <p className="tiny mt-1">
                        {data.total} {t("dir.films_n")}
                        {data.avg_score != null ? ` · ${data.avg_score} ${t("dir.avg_q")}` : ""}
                    </p>
                </div>
            </div>

            <div className="mb-4 flex flex-wrap gap-1.5">
                {(["popularity", "year", "score", "title"] as DirectorSort[]).map((s) => (
                    <button
                        key={s}
                        onClick={() => setSort(s)}
                        className={
                            "border px-2.5 py-1 font-mono text-[11px] transition-colors " +
                            (sort === s
                                ? "border-primary bg-primary text-bg"
                                : "border-border-2 text-fg-3 hover:text-fg")
                        }
                    >
                        {t(`director.sort_${s}`)}
                    </button>
                ))}
            </div>

            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
                {data.films.map((f) => (
                    <Link
                        key={f.tmdb_id}
                        href={`/movie/${f.tmdb_id}`}
                        className="group flex flex-col gap-1.5"
                    >
                        <div className="relative aspect-[2/3] overflow-hidden border border-border-2 bg-bg-2">
                            {f.poster_path ? (
                                <Image
                                    src={getTMDBImageUrl(f.poster_path, "w342") || ""}
                                    alt={f.title}
                                    fill
                                    sizes="(max-width:640px) 50vw, (max-width:1024px) 25vw, 16vw"
                                    className="object-cover transition-transform group-hover:scale-105"
                                />
                            ) : (
                                <div className="flex h-full items-center justify-center font-mono text-[10px] text-fg-3">
                                    {f.title}
                                </div>
                            )}
                        </div>
                        <div className="min-w-0">
                            <p className="truncate font-mono text-[11px] text-fg">{language === "es" && f.title_es ? f.title_es : f.title}</p>
                            <p className="font-mono text-[10px] text-fg-3">
                                {[f.year, f.vectorbox_score != null ? `Q${Math.round(f.vectorbox_score)}` : null]
                                    .filter(Boolean)
                                    .join(" · ")}
                            </p>
                        </div>
                    </Link>
                ))}
            </div>
        </div>
    );
}
