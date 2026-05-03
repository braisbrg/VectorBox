"use client";

import { useState, useEffect, useCallback } from "react";

/**
 * 15-tag grid for content preferences.
 * Two-state toggle: neutral ↔ avoided.
 * Used by both /onboarding/tags (localStorage) and Settings (API).
 */

export const ONBOARDING_TAGS = [
    "Jumpscares",
    "Gore",
    "Terror psicológico",
    "Contenido adulto",
    "Temáticas oscuras",
    "Ritmo muy lento",
    "Películas +3h",
    "Animación",
    "Documentales",
    "Mudas / B&N",
    "Musicales",
    "Contenido familiar",
    "Ciencia ficción dura",
    "Basadas en hechos reales",
    "Cine de superhéroes",
] as const;

export type TagState = "neutral" | "avoided";

export interface TagPreferences {
    avoided: string[];
}

interface TagSelectorProps {
    value: Record<string, TagState>;
    onChange: (next: Record<string, TagState>) => void;
    /** If true, renders compact for settings view */
    compact?: boolean;
}

// Cycle order: neutral → avoided → neutral.
// Avoid-first matches the original spec wireframe — first click expresses
// the most common signal (rule out content).
const STATE_CYCLE: Record<TagState, TagState> = {
    neutral: "avoided",
    avoided: "neutral",
};

const STATE_STYLES: Record<TagState, string> = {
    neutral:
        "border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:text-zinc-300",
    avoided:
        "border-red-500/60 text-red-400 bg-red-500/10 line-through decoration-red-500/40",
};

const STATE_LABELS: Record<TagState, string> = {
    neutral: "",
    avoided: "✕",
};

export function TagSelector({ value, onChange, compact = false }: TagSelectorProps) {
    const cycle = useCallback(
        (tag: string) => {
            const current = value[tag] || "neutral";
            const next = STATE_CYCLE[current];
            onChange({ ...value, [tag]: next });
        },
        [value, onChange]
    );

    return (
        <div className="space-y-3">
            {!compact && (
                <div className="flex items-center gap-4 text-[10px] font-mono text-zinc-600 uppercase tracking-widest">
                    <span className="flex items-center gap-1">
                        <span className="w-2 h-2 border border-red-500/60 bg-red-500/20" />
                        Avoid
                    </span>
                    <span className="flex items-center gap-1">
                        <span className="w-2 h-2 border border-zinc-700" />
                        Neutral
                    </span>
                </div>
            )}
            <div
                className={`grid gap-2 ${
                    compact
                        ? "grid-cols-2 sm:grid-cols-3"
                        : "grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5"
                }`}
            >
                {ONBOARDING_TAGS.map((tag) => {
                    const state = value[tag] || "neutral";
                    return (
                        <button
                            key={tag}
                            type="button"
                            title={tag}
                            onClick={() => cycle(tag)}
                            className={`
                                group relative px-3 py-2 border font-mono text-xs uppercase tracking-wide
                                transition-all duration-150 cursor-pointer select-none min-w-[110px]
                                ${STATE_STYLES[state]}
                            `}
                        >
                            <span className="flex items-center justify-between gap-1">
                                <span className="truncate">{tag}</span>
                                {state !== "neutral" && (
                                    <span className="text-[10px] opacity-80 shrink-0">
                                        {STATE_LABELS[state]}
                                    </span>
                                )}
                            </span>
                        </button>
                    );
                })}
            </div>
        </div>
    );
}

/**
 * Helper to convert TagSelector state map → TagPreferences for API/localStorage.
 */
export function tagStateToPreferences(
    states: Record<string, TagState>
): TagPreferences {
    return {
        avoided: Object.entries(states)
            .filter(([, v]) => v === "avoided")
            .map(([k]) => k),
    };
}

/**
 * Helper to convert TagPreferences → TagSelector state map.
 */
export function preferencesToTagState(
    prefs: TagPreferences | null
): Record<string, TagState> {
    const result: Record<string, TagState> = {};
    ONBOARDING_TAGS.forEach((t) => {
        if (prefs?.avoided?.includes(t)) result[t] = "avoided";
        else result[t] = "neutral";
    });
    return result;
}
