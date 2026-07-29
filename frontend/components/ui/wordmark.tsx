// The VectorBox wordmark. One component because it appeared verbatim in six
// places (landing, explore, onboarding, /try, and twice in auth-panel) and a
// duplicated mark is a mark that drifts.
//
// Design 2026-07-28: the trident glyph — the three engine signals, the only
// piece of the identity that comes from the product rather than from a trend —
// followed by VECTOR in foreground and BOX in the accent. It replaces the filled
// yellow box: same two-tone idea, but the accent now reads as type instead of a
// block, which sits better next to the trident and inverts cleanly on light
// grounds.
//
// The trident scales in `em`, so the mark works at any font-size the call site
// sets without a second variant.

/** The trident on its own — the collapsed sidebar and anywhere the name will not fit. */
export function TridentMark({ className = "" }: { className?: string }) {
    return (
        <span className={`inline-flex items-end gap-[0.14em] ${className}`} aria-hidden="true">
            <i className="block h-[0.78em] w-[0.2em] bg-fg" />
            <i className="block h-[1.1em] w-[0.2em] bg-primary" />
            <i className="block h-[0.78em] w-[0.2em] bg-fg" />
        </span>
    );
}

export function Wordmark({ className = "" }: { className?: string }) {
    return (
        <span className={`inline-flex items-center gap-[0.45em] ${className}`}>
            <TridentMark />
            <span>
                <span className="text-fg">VECTOR</span>
                <span className="text-primary">BOX</span>
            </span>
        </span>
    );
}
