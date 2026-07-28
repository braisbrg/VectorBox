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

export function Wordmark({ className = "" }: { className?: string }) {
    return (
        <span className={`inline-flex items-center gap-[0.45em] ${className}`}>
            <span className="inline-flex items-end gap-[0.14em]" aria-hidden="true">
                <i className="block w-[0.2em] h-[0.78em] bg-fg" />
                <i className="block w-[0.2em] h-[1.1em] bg-primary" />
                <i className="block w-[0.2em] h-[0.78em] bg-fg" />
            </span>
            <span>
                <span className="text-fg">VECTOR</span>
                <span className="text-primary">BOX</span>
            </span>
        </span>
    );
}
