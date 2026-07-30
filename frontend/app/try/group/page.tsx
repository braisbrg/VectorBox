import Link from "next/link";

// Group rec needs an account: the overlap endpoint requires the requester to
// be a group member (H-2 anti-enumeration guard — otherwise anyone could probe
// arbitrary pairs of users' watchlist intersections). Honest explainer + CTA.
export default function TryGroupPage() {
    return (
        <div className="mx-auto max-w-md pt-16">
            <div className="border border-border-2 bg-bg-2 p-8 text-center shadow-acid">
                <p className="font-display text-4xl text-primary">◇</p>
                <h1 className="mt-4 font-display text-2xl uppercase tracking-tight text-fg">group rec</h1>
                <p className="mt-3 font-mono text-[11px] leading-relaxed text-fg-3">
                    paste 2–6 letterboxd handles and find the film your whole table will sit through — the
                    peer-agreement matrix shows who&apos;s already seen what.
                </p>
                <p className="mt-3 border border-dashed border-border-2 p-3 font-mono text-[10px] leading-relaxed text-fg-3">
                    this one needs an account: you must be a member of the group you query, so strangers
                    can&apos;t probe other people&apos;s watchlists.
                </p>
                <Link
                    href="/register"
                    className="mt-5 inline-block bg-primary px-5 py-2.5 font-display text-[11px] font-bold uppercase tracking-[0.1em] text-primary-ink"
                >
                    create account →
                </Link>
            </div>
        </div>
    );
}
