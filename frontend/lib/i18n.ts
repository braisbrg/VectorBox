export type Language = "en" | "es";

import en from "@/messages/en.json";
import es from "@/messages/es.json";

export const translations = {
    en,
    es,
};

export const SUPPORTED_LOCALES: Language[] = ["en", "es"];
export const DEFAULT_LOCALE: Language = "en";

export function isLanguage(value: unknown): value is Language {
    return value === "en" || value === "es";
}

/**
 * Pick the UI language from an Accept-Language header (the browser/OS
 * preference — NOT geolocation). Honours q-weights and matches on the primary
 * subtag (es-ES / es-419 -> es, en-US -> en). Falls back to DEFAULT_LOCALE.
 * Pure and edge-safe (no Node APIs) so it can run inside middleware.
 */
export function resolveLocale(acceptLanguage?: string | null): Language {
    if (!acceptLanguage) return DEFAULT_LOCALE;
    const ranked = acceptLanguage
        .split(",")
        .map((part) => {
            const [tag, ...params] = part.trim().split(";");
            const qParam = params.find((p) => p.trim().startsWith("q="));
            const q = qParam ? parseFloat(qParam.split("=")[1]) : 1;
            return { base: tag.trim().toLowerCase().split("-")[0], q: Number.isNaN(q) ? 0 : q };
        })
        .filter((entry) => entry.base)
        .sort((a, b) => b.q - a.q);
    for (const { base } of ranked) {
        const match = SUPPORTED_LOCALES.find((locale) => locale === base);
        if (match) return match;
    }
    return DEFAULT_LOCALE;
}
