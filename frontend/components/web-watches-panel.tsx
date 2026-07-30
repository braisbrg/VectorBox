"use client";

/**
 * F-22 — Web Watches → Letterboxd export panel.
 *
 * VectorBox can't write to a user's Letterboxd account, so when they mark
 * a film as watched here (`watch_count=0` sentinel) it stays only in our
 * DB. This panel surfaces those films so the user can either:
 *   - manually mark them on Letterboxd (clicking the linked URI), or
 *   - download a CSV in Letterboxd's diary-import format and drop it
 *     into https://letterboxd.com/import/ in one step.
 *
 * Both flows are served by `/api/recommendations/movies/watched-on-web`
 * (JSON list) and `.csv` variants on the backend.
 */
import Image from "next/image";
import { useQuery } from "@tanstack/react-query";
import { Download, ExternalLink, Loader2 } from "lucide-react";
import { api, getTMDBImageUrl } from "@/lib/api";

interface WebWatch {
    tmdb_id: number;
    title: string;
    year: number | null;
    letterboxd_uri: string | null;
    poster_path: string | null;
    watched_date: string | null;
}

export function WebWatchesPanel() {
    const { data, isLoading, error } = useQuery<WebWatch[]>({
        queryKey: ["web-watches"],
        queryFn: async () => {
            const { data } = await api.get<WebWatch[]>(
                "/api/recommendations/movies/watched-on-web",
            );
            return data;
        },
        staleTime: 60 * 1000, // 1 min — these change only when the user marks
    });

    if (isLoading) {
        return (
            <div className="border border-border p-4 font-mono">
                <h3 className="text-xs text-fg-2 mb-2">WEB WATCHES</h3>
                <div className="flex items-center gap-2 text-fg-3 text-sm">
                    <Loader2 className="size-4 animate-spin" /> Loading…
                </div>
            </div>
        );
    }

    if (error || !data) {
        return (
            <div className="border border-border p-4 font-mono">
                <h3 className="text-xs text-fg-2 mb-2">WEB WATCHES</h3>
                <p className="text-xs text-danger">Could not load watched-on-web list.</p>
            </div>
        );
    }

    if (data.length === 0) {
        return (
            <div className="border border-border p-4 font-mono">
                <h3 className="text-xs text-fg-2 mb-2">WEB WATCHES</h3>
                <p className="text-xs text-fg-3">
                    Films you mark as watched in VectorBox will appear here so you can mirror
                    them back to your Letterboxd account. Nothing to show yet.
                </p>
            </div>
        );
    }

    return (
        <div className="border border-border p-4 font-mono space-y-3">
            <div className="flex items-center justify-between gap-3 flex-wrap">
                <div>
                    <h3 className="text-xs text-fg-2">WEB WATCHES</h3>
                    <p className="text-[11px] text-fg-3 mt-1">
                        {data.length} film{data.length === 1 ? "" : "s"} you marked here. Not yet on
                        your Letterboxd account — pick a flow below.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={async () => {
                        // Plain <a href> navigation doesn't carry the
                        // Authorization: Bearer header AuthBridge injects via
                        // axios — Clerk JWT lives in memory, not in cookies.
                        // Fetch through `api`, then trigger the download from
                        // an in-memory Blob URL.
                        const res = await api.get(
                            "/api/recommendations/movies/watched-on-web.csv",
                            { responseType: "blob" },
                        );
                        const url = URL.createObjectURL(res.data);
                        const anchor = document.createElement("a");
                        anchor.href = url;
                        anchor.download = "vectorbox-watched.csv";
                        document.body.appendChild(anchor);
                        anchor.click();
                        anchor.remove();
                        URL.revokeObjectURL(url);
                    }}
                    className="inline-flex items-center gap-2 px-3 py-1.5 border border-primary/30 text-xs text-primary hover:bg-primary/10 transition-colors uppercase tracking-wider"
                >
                    <Download className="size-3.5" />
                    Letterboxd CSV
                </button>
            </div>

            <p className="text-[10px] text-fg-3 leading-relaxed">
                Drop the CSV into{" "}
                <a
                    href="https://letterboxd.com/import/"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline text-primary"
                >
                    letterboxd.com/import
                </a>{" "}
                or click each title below to mark it manually.
            </p>

            <ul className="divide-y divide-border max-h-80 overflow-y-auto">
                {data.map((film) => (
                    <li key={film.tmdb_id} className="flex items-center gap-3 py-2">
                        {film.poster_path ? (
                            <Image
                                src={getTMDBImageUrl(film.poster_path, "w92")}
                                alt={film.title}
                                width={36}
                                height={54}
                                className="flex-shrink-0 border border-border"
                                unoptimized
                            />
                        ) : (
                            <div className="w-9 h-[54px] bg-zinc-900 flex-shrink-0 border border-border" />
                        )}
                        <div className="flex-1 min-w-0">
                            <p className="text-xs text-fg truncate">
                                {film.title} {film.year ? `(${film.year})` : ""}
                            </p>
                            <p className="text-[10px] text-fg-3">
                                marked {film.watched_date ?? "—"}
                            </p>
                        </div>
                        {film.letterboxd_uri && (
                            <a
                                href={film.letterboxd_uri}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-fg-2 hover:text-primary"
                                title="Open on Letterboxd"
                            >
                                <ExternalLink className="size-3.5" />
                            </a>
                        )}
                    </li>
                ))}
            </ul>
        </div>
    );
}
