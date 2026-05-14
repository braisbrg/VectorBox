"use client";

import Link from "next/link";
import Image from "next/image";
import { useState, useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, Undo2, Ban } from "lucide-react";
import { useLanguage } from "@/components/language-provider";
import { useSettings } from "@/lib/hooks";
import { syncRSS, VectorboxUser, getRejectedMovies, unrejectMovie, getTMDBImageUrl } from "@/lib/api";
import { api } from "@/lib/api";
import { UploadZone } from "@/components/upload-zone";
import {
    TagSelector,
    TagState,
    tagStateToPreferences,
    preferencesToTagState,
} from "@/components/onboarding/tag-selector";
import { Sliders } from "lucide-react";

export function SettingsView() {
    const { t } = useLanguage();
    const { mounted } = useSettings();
    const [letterboxdUsername, setLetterboxdUsername] = useState<string | null>(null);
    const [currentUser, setCurrentUser] = useState<VectorboxUser | null>(null);
    const [syncMessage, setSyncMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
    const [showReupload, setShowReupload] = useState(false);
    const queryClient = useQueryClient();

    useEffect(() => {
        try {
            const user = JSON.parse(localStorage.getItem("vectorbox_user") || "{}");
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
    }, []);

    const syncMutation = useMutation({
        mutationFn: (username: string) => syncRSS(username),
        onSuccess: () => {
            setSyncMessage({ type: "success", text: "Sync started - your feed will update shortly" });
        },
        onError: (error: Error) => {
            setSyncMessage({ type: "error", text: error.message || "Sync failed. Please try again." });
        },
    });

    // Not Interested - rejected movies
    const {
        data: rejectedMovies,
        isLoading: rejectedLoading,
    } = useQuery({
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

    if (!mounted) {
        return null;
    }

    return (
        <div className="max-w-2xl mx-auto p-6 bg-card border rounded-xl">
            <h2 className="text-2xl font-bold mb-4">{t("settings.title")}</h2>
            <p className="text-muted-foreground mb-6">{t("settings.subtitle")}</p>

            <div className="space-y-6">
                {/* Letterboxd Sync */}
                {letterboxdUsername && (
                    <div className="p-4 border border-primary/30 rounded-lg space-y-3">
                        <div className="flex items-center justify-between">
                            <div className="space-y-0.5">
                                <h3 className="font-medium text-zinc-200">Letterboxd Sync</h3>
                                <p className="text-xs text-zinc-500">
                                    Sync your latest ratings and watchlist from <span className="text-primary">{letterboxdUsername}</span>
                                </p>
                            </div>
                            <button
                                onClick={() => syncMutation.mutate(letterboxdUsername)}
                                disabled={syncMutation.isPending}
                                className="flex items-center gap-2 px-4 py-2 rounded-lg border border-primary/30 text-sm font-medium text-primary hover:bg-primary/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                {syncMutation.isPending ? (
                                    <Loader2 className="size-4 animate-spin" />
                                ) : (
                                    <RefreshCw className="size-4" />
                                )}
                                Sync Letterboxd Now
                            </button>
                        </div>

                        {syncMessage && (
                            <p className={`text-sm ${syncMessage.type === "success" ? "text-primary" : "text-red-500"}`}>
                                {syncMessage.text}
                            </p>
                        )}
                    </div>
                )}

                {/* Letterboxd Data - Re-upload */}
                {currentUser && (
                    <div className="border border-border p-4 font-mono">
                        <h3 className="text-xs text-zinc-400 mb-2">LETTERBOXD DATA</h3>
                        <p className="text-xs text-zinc-500 mb-3">
                            Re-upload your Letterboxd export to sync your full history.
                            This will replace your current data.
                        </p>
                        {!showReupload ? (
                            <button
                                onClick={() => setShowReupload(true)}
                                className="text-xs font-mono border border-border px-3 py-1.5 hover:border-primary hover:text-primary transition-colors"
                            >
                                [ RE-UPLOAD EXPORT ]
                            </button>
                        ) : (
                            <div className="mt-2 space-y-3">
                                <button
                                    onClick={() => setShowReupload(false)}
                                    className="text-xs font-mono text-zinc-600 hover:text-zinc-400 transition-colors"
                                >
                                    [ CANCEL ]
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
                    </div>
                )}

                {/* Not Interested - Rejected Movies History */}
                <div className="border border-border p-4 font-mono">
                    <div className="flex items-center gap-2 mb-2">
                        <Ban className="size-3.5 text-zinc-400" />
                        <h3 className="text-xs text-zinc-400">NOT INTERESTED</h3>
                    </div>
                    <p className="text-xs text-zinc-500 mb-3">
                        Movies you&apos;ve dismissed. Undo to let them appear in recommendations again.
                    </p>

                    {rejectedLoading ? (
                        <div className="flex items-center gap-2 text-xs text-zinc-600">
                            <Loader2 className="size-3 animate-spin" />
                            Loading…
                        </div>
                    ) : !rejectedMovies || rejectedMovies.length === 0 ? (
                        <p className="text-xs text-zinc-600">No rejected movies yet.</p>
                    ) : (
                        <div className="space-y-2 max-h-[320px] overflow-y-auto scrollbar-hide">
                            {rejectedMovies.map((movie) => (
                                <div
                                    key={movie.tmdb_id}
                                    className="flex items-center gap-3 p-2 border border-zinc-800 hover:border-zinc-600 transition-colors group"
                                >
                                    {/* Poster thumbnail */}
                                    <div className="w-8 h-12 flex-shrink-0 bg-zinc-900 overflow-hidden">
                                        {movie.poster_path ? (
                                            <Image
                                                src={getTMDBImageUrl(movie.poster_path, "w92")}
                                                alt={movie.title}
                                                width={32}
                                                height={48}
                                                className="size-full object-cover"
                                            />
                                        ) : (
                                            <div className="size-full flex items-center justify-center text-zinc-700 text-[8px]">
                                                N/A
                                            </div>
                                        )}
                                    </div>

                                    {/* Title + year */}
                                    <div className="flex-1 min-w-0">
                                        <p className="text-xs text-zinc-300 truncate">{movie.title}</p>
                                        {movie.year && (
                                            <p className="text-[10px] text-zinc-600">{movie.year}</p>
                                        )}
                                    </div>

                                    {/* Undo button */}
                                    <button
                                        onClick={() => unrejectMutation.mutate(movie.tmdb_id)}
                                        disabled={unrejectMutation.isPending}
                                        className="flex items-center gap-1 px-2 py-1 text-[10px] font-mono text-zinc-600 hover:text-primary border border-transparent hover:border-primary transition-colors opacity-0 group-hover:opacity-100"
                                        title="Undo rejection"
                                    >
                                        <Undo2 className="size-3" />
                                        UNDO
                                    </button>
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                {/* Content Preferences - Tag Editor */}
                <ContentPreferencesSection />

                {/* About Section */}
                <div className="p-4 border rounded-lg bg-muted/30">
                    <h3 className="font-medium mb-2">{t("settings.about.title")}</h3>
                    <p className="text-sm text-muted-foreground">
                        {t("settings.about.desc")}
                    </p>
                    <p className="text-xs text-muted-foreground mt-2">
                        {t("settings.about.version")}
                    </p>
                </div>

                {/* Legal Links */}
                <div className="flex items-center justify-center gap-4 pt-2">
                    <Link
                        href="/privacy"
                        className="text-xs font-mono text-zinc-600 hover:text-[#CCFF00] transition-colors uppercase tracking-wider"
                    >
                        Privacy Policy
                    </Link>
                    <span className="text-zinc-700">·</span>
                    <Link
                        href="/terms"
                        className="text-xs font-mono text-zinc-600 hover:text-[#CCFF00] transition-colors uppercase tracking-wider"
                    >
                        Terms of Service
                    </Link>
                </div>
            </div>
        </div>
    );
}

/**
 * CONTENT PREFERENCES section - fetches from /onboarding/status, saves via /onboarding/tags.
 */
function ContentPreferencesSection() {
    const [tagStates, setTagStates] = useState<Record<string, TagState>>({});
    const [loaded, setLoaded] = useState(false);
    const [saving, setSaving] = useState(false);
    const saveTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

    // Fetch current preferences
    useEffect(() => {
        api.get("/api/onboarding/status")
            .then((res) => {
                const prefs = res.data?.tag_preferences;
                setTagStates(preferencesToTagState(prefs || null));
            })
            .catch(() => {
                // No prefs yet - show empty
            })
            .finally(() => setLoaded(true));
    }, []);

    const handleChange = (next: Record<string, TagState>) => {
        setTagStates(next);
        // Debounced save
        if (saveTimeout.current) clearTimeout(saveTimeout.current);
        saveTimeout.current = setTimeout(async () => {
            setSaving(true);
            try {
                const prefs = tagStateToPreferences(next);
                await api.post("/api/onboarding/tags", prefs);
            } catch (e) {
                console.error("Failed to save tag preferences:", e);
            } finally {
                setSaving(false);
            }
        }, 600);
    };

    if (!loaded) return null;

    return (
        <div className="border border-border p-4 font-mono">
            <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                    <Sliders className="size-3.5 text-zinc-400" />
                    <h3 className="text-xs text-zinc-400">CONTENT PREFERENCES</h3>
                </div>
                {saving && (
                    <span className="text-[10px] text-zinc-600 animate-pulse">Saving...</span>
                )}
            </div>
            <p className="text-xs text-zinc-500 mb-3">
                Tap to cycle: neutral → like → avoid. Preferences influence your recommendations.
            </p>
            <TagSelector value={tagStates} onChange={handleChange} compact />
        </div>
    );
}
