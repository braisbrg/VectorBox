"use client";

// Cuatro estados de ánimo sobre dos ejes ortogonales (gravedad × humanidad),
// desplegable dentro de SYS_CONSOLE.
//
// No son sliders a propósito: "gravedad 0-100" no se lo cree nadie, "algo
// reconfortante" sí, y por debajo es el mismo par de rangos. Los cuadrantes
// viven en backend/services/mood_axes.py — aquí sólo se nombran.
//
// El mood FILTRA, no puntúa: medido, como rasgo de ranking pierde contra el
// embedding del que sale. Lo que aporta es pilotar algo que tu historial no
// contiene — qué te apetece hoy.
//
// ponytail: <details> nativo en vez de un colapsable en estado de React. Trae
// el toggle, el teclado y el aria-expanded ya hechos.

import { useShell } from "@/components/shell/shell-context";
import { useLanguage } from "@/components/language-provider";
import { cn } from "@/lib/utils";

// `deep_cut` va detrás de `popcorn` a propósito: son la misma caja de ánimo
// partida por la popularidad, y leerlas seguidas lo hace evidente.
const MOODS = ["moving", "dark", "comforting", "popcorn", "deep_cut"] as const;

export function MoodChips() {
    const { activeMood, setMood, isFiltering } = useShell();
    const { t } = useLanguage();

    return (
        // Abierto de entrada si hay un ánimo puesto: si no, el feed sale filtrado
        // y el motivo queda escondido detrás de un triángulo.
        <details open={!!activeMood} className="mb-4 border border-border-2 bg-bg-2">
            <summary className="flex cursor-pointer items-center justify-between px-3 py-2 font-display text-[9px] uppercase tracking-[0.15em] text-fg-3 hover:text-fg-2">
                <span>{t("mood.prompt")}</span>
                {activeMood && (
                    <span className="font-mono text-[9px] normal-case text-primary">
                        {t(`mood.${activeMood}`)}
                    </span>
                )}
            </summary>

            <div className="flex flex-wrap gap-1.5 border-t border-border-2 p-3">
                {MOODS.map((m) => {
                    const active = activeMood === m;
                    return (
                        <button
                            key={m}
                            type="button"
                            disabled={isFiltering}
                            // Volver a pulsar el chip activo vuelve al feed normal: sin
                            // eso el único modo de salir sería el RESET del rail, que
                            // no es evidente que también quite el ánimo.
                            onClick={() => setMood(active ? null : m)}
                            aria-pressed={active}
                            className={cn(
                                "border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide transition-colors disabled:opacity-50",
                                active
                                    ? "border-primary bg-primary font-bold text-primary-ink"
                                    : "border-border-2 text-fg-3 hover:border-fg-3 hover:text-fg-2"
                            )}
                        >
                            {t(`mood.${m}`)}
                        </button>
                    );
                })}
            </div>
        </details>
    );
}
