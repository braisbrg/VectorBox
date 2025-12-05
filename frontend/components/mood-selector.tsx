"use client";

import { useQuery } from "@tanstack/react-query";
import { getUserClusters, ClusterInfo } from "@/lib/api";
import { motion } from "framer-motion";
import { Loader2 } from "lucide-react";

interface MoodSelectorProps {
    userId: number;
    onSelectCluster: (clusterId: number | null) => void;
    selectedClusterId?: number | null;
}

const moodEmojis: Record<string, string> = {
    horror: "🎃",
    drama: "🎭",
    comedy: "😂",
    action: "💥",
    scifi: "🚀",
    romance: "💕",
    thriller: "🔪",
    default: "🎬",
};

function getMoodEmoji(label: string): string {
    const lowerLabel = label.toLowerCase();
    for (const [key, emoji] of Object.entries(moodEmojis)) {
        if (lowerLabel.includes(key)) return emoji;
    }
    return moodEmojis.default;
}

export function MoodSelector({ userId, onSelectCluster, selectedClusterId }: MoodSelectorProps) {
    const { data: clusters, isLoading, error } = useQuery({
        queryKey: ["clusters", userId],
        queryFn: () => getUserClusters(userId),
        retry: 2,
    });

    if (isLoading) {
        return (
            <div className="flex items-center justify-center py-12">
                <Loader2 className="w-8 h-8 animate-spin text-primary" />
            </div>
        );
    }

    if (error) {
        return (
            <div className="p-6 bg-destructive/10 border border-destructive/20 rounded-lg">
                <p className="text-destructive">
                    Failed to load your taste clusters. Please try uploading your data again.
                </p>
            </div>
        );
    }

    if (!clusters || clusters.length === 0) {
        return (
            <div className="p-6 bg-muted/50 rounded-lg">
                <p className="text-muted-foreground">
                    No clusters found. Processing your data...
                </p>
            </div>
        );
    }

    return (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {clusters.map((cluster, index) => {
                const isSelected = selectedClusterId === cluster.cluster_id;

                return (
                    <motion.button
                        key={cluster.cluster_id}
                        initial={{ opacity: 0, y: 20, borderColor: "hsl(var(--border))" }}
                        animate={{
                            opacity: 1,
                            y: 0,
                            scale: isSelected ? 1.02 : 1,
                            borderColor: isSelected ? "hsl(var(--primary))" : "hsl(var(--border))"
                        }}
                        transition={{ delay: index * 0.1 }}
                        whileHover={{ scale: isSelected ? 1.02 : 1.05 }}
                        whileTap={{ scale: 0.95 }}
                        onClick={() => onSelectCluster(isSelected ? null : cluster.cluster_id)}
                        className={`p-6 rounded-lg bg-card border-2 transition-all text-left group relative overflow-hidden ${isSelected ? "ring-2 ring-primary ring-offset-2 ring-offset-background shadow-lg" : "hover:border-primary/50"
                            }`}
                    >
                        {isSelected && (
                            <div className="absolute top-0 right-0 p-2 bg-primary text-primary-foreground rounded-bl-lg text-xs font-bold">
                                Selected
                            </div>
                        )}

                        <div className="flex items-start justify-between mb-4">
                            <div className="text-4xl">{getMoodEmoji(cluster.label)}</div>
                            <div className="text-right">
                                <div className="text-2xl font-bold text-primary">
                                    {cluster.avg_rating.toFixed(1)}
                                </div>
                                <div className="text-xs text-muted-foreground">avg rating</div>
                            </div>
                        </div>

                        <h4 className="font-bold text-lg mb-2 group-hover:text-primary transition-colors">
                            {cluster.label}
                        </h4>

                        <div className="flex flex-wrap gap-1 mb-3">
                            {cluster.dominant_genres.slice(0, 3).map((genre) => (
                                <span
                                    key={genre}
                                    className="text-xs px-2 py-1 bg-primary/10 text-primary rounded-full"
                                >
                                    {genre}
                                </span>
                            ))}
                        </div>

                        <p className="text-sm text-muted-foreground">
                            {cluster.movie_count} movies in this cluster
                        </p>

                        {cluster.sample_movies.length > 0 && (
                            <div className="mt-3 pt-3 border-t border-border">
                                <p className="text-xs text-muted-foreground mb-1">Sample movies:</p>
                                <div className="flex flex-wrap gap-1">
                                    {cluster.sample_movies.slice(0, 3).map((m, i) => (
                                        <a
                                            key={m.tmdb_id}
                                            href={`https://letterboxd.com/tmdb/${m.tmdb_id}`}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            onClick={(e) => e.stopPropagation()}
                                            className="text-xs font-medium hover:text-primary hover:underline truncate max-w-full block"
                                        >
                                            {m.title}{i < Math.min(cluster.sample_movies.length, 3) - 1 ? ", " : ""}
                                        </a>
                                    ))}
                                </div>
                            </div>
                        )}
                    </motion.button>
                );
            })}
        </div>
    );
}
