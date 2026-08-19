"use client";

import { useCallback } from "react";

import { useLanguage } from "@/components/language-provider";

/**
 * 15-tag grid for content preferences.
 * Two-state toggle: neutral ↔ avoided.
 * Used by both /onboarding/tags (localStorage) and Settings (API).
 */

// The tag STRING is the API contract — it's stored in `tag_preferences.avoided`
// and matched verbatim by the backend, so it must NEVER change. TAG_LABELS is
// display-only: the user sees a localized label, the canonical value stays put.
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

// Display labels per locale (colocated with the enum so adding a tag forces
// adding its labels — no separate parity-checked message keys for data values).
const TAG_LABELS: Record<string, { en: string; es: string }> = {
    "Jumpscares": { en: "Jump scares", es: "Sustos" },
    "Gore": { en: "Gore", es: "Gore" },
    "Terror psicológico": { en: "Psychological horror", es: "Terror psicológico" },
    "Contenido adulto": { en: "Adult content", es: "Contenido adulto" },
    "Temáticas oscuras": { en: "Dark themes", es: "Temáticas oscuras" },
    "Ritmo muy lento": { en: "Very slow pace", es: "Ritmo muy lento" },
    "Películas +3h": { en: "Films over 3h", es: "Películas +3h" },
    "Animación": { en: "Animation", es: "Animación" },
    "Documentales": { en: "Documentaries", es: "Documentales" },
    "Mudas / B&N": { en: "Silent / B&W", es: "Mudas / B&N" },
    "Musicales": { en: "Musicals", es: "Musicales" },
    "Contenido familiar": { en: "Family content", es: "Contenido familiar" },
    "Ciencia ficción dura": { en: "Hard sci-fi", es: "Ciencia ficción dura" },
    "Basadas en hechos reales": { en: "Based on true events", es: "Basadas en hechos reales" },
    "Cine de superhéroes": { en: "Superhero films", es: "Cine de superhéroes" },
};

/** Localized display label for a tag; falls back to the raw value if unmapped. */
export function tagLabel(tag: string, language: string): string {
    return TAG_LABELS[tag]?.[language === "es" ? "es" : "en"] ?? tag;
}

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
// Avoid-first matches the original spec wireframe - first click expresses
// the most common signal (rule out content).
const STATE_CYCLE: Record<TagState, TagState> = {
    neutral: "avoided",
    avoided: "neutral",
};

const STATE_STYLES: Record<TagState, string> = {
    neutral:
        "border-border-2 text-fg-2 hover:border-fg-3 hover:text-fg-2",
    avoided:
        "border-danger/60 text-danger bg-danger/10 line-through decoration-danger/40",
};

const STATE_LABELS: Record<TagState, string> = {
    neutral: "",
    avoided: "✕",
};

export function TagSelector({ value, onChange, compact = false }: TagSelectorProps) {
    const { language, t } = useLanguage();
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
                <div className="flex items-center gap-4 text-[10px] font-mono text-fg-3 uppercase tracking-widest">
                    <span className="flex items-center gap-1">
                        <span className="size-2 border border-danger/60 bg-danger/20" />
                        {t("gonb.tag_avoid")}
                    </span>
                    <span className="flex items-center gap-1">
                        <span className="size-2 border border-border-2" />
                        {t("gonb.tag_neutral")}
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
                            title={tagLabel(tag, language)}
                            onClick={() => cycle(tag)}
                            className={`
                                group relative px-3 py-2 border font-mono text-xs uppercase tracking-wide
                                transition-colors duration-150 cursor-pointer select-none
                                ${STATE_STYLES[state]}
                            `}
                        >
                            {/* La etiqueta ENVUELVE, no se trunca: "basadas en hechos reales"
                                pide ~195px y la celda da ~180, así que con truncate salía
                                del botón encima de la vecina (min-width:auto de flex). El
                                grid iguala la altura de la fila, así que envolver no descuadra. */}
                            <span className="flex min-w-0 items-start justify-between gap-1 text-left">
                                <span>{tagLabel(tag, language)}</span>
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
        avoided: Object.entries(states).reduce((acc, [k, v]) => {
            if (v === "avoided") acc.push(k);
            return acc;
        }, [] as string[]),
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
