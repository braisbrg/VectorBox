// Legal page shell — handoff legal layout: sticky "on this page" TOC (left),
// acid zero-padded section numbers, lime rule under the updated line.
// Content lives in the pages; this only owns structure + skin.

import Link from "next/link";

export interface LegalSection {
    id: string;
    title: string;
    body: React.ReactNode;
}

export function LegalShell({
    title,
    updated,
    sibling,
    sections,
}: {
    title: string;
    updated: string;
    sibling: { href: string; label: string };
    sections: LegalSection[];
}) {
    return (
        <main className="min-h-screen bg-bg text-fg-2">
            <div className="mx-auto max-w-5xl px-6 py-16">
                <Link href="/" className="mb-10 inline-block font-mono text-sm text-primary hover:underline">
                    ← Back
                </Link>

                <h1 className="font-display text-3xl uppercase tracking-tight text-fg sm:text-4xl">{title}</h1>
                <p className="mt-3 border-b-2 border-primary pb-4 font-mono text-xs text-fg-3">last updated: {updated}</p>

                <div className="mt-10 grid grid-cols-1 gap-10 lg:grid-cols-[200px_1fr]">
                    {/* sticky TOC */}
                    <nav className="self-start lg:sticky lg:top-10">
                        <div className="eyebrow mb-3">on this page</div>
                        <ol className="space-y-1.5">
                            {sections.map((s, i) => (
                                <li key={s.id}>
                                    <a
                                        href={`#${s.id}`}
                                        className="font-mono text-[11px] text-fg-3 transition-colors hover:text-primary"
                                    >
                                        <span className="text-primary">{String(i + 1).padStart(2, "0")}</span> {s.title}
                                    </a>
                                </li>
                            ))}
                        </ol>
                        <div className="mt-6 border-t border-border-2 pt-3">
                            <Link href={sibling.href} className="font-mono text-[11px] text-fg-3 hover:text-primary">
                                also → {sibling.label}
                            </Link>
                        </div>
                    </nav>

                    {/* sections */}
                    <div>
                        {sections.map((s, i) => (
                            <section key={s.id} id={s.id} className="mb-8 scroll-mt-20 border-b border-border-2 pb-8 last:mb-0 last:border-0">
                                <h2 className="mb-4 font-display text-sm uppercase tracking-widest text-fg">
                                    <span className="text-primary">{String(i + 1).padStart(2, "0")}</span> · {s.title}
                                </h2>
                                {s.body}
                            </section>
                        ))}
                    </div>
                </div>
            </div>
        </main>
    );
}

/** Bordered mono info box (entity blocks, cookie tables). */
export function LegalBox({ children }: { children: React.ReactNode }) {
    return <div className="space-y-1 border border-border-2 bg-bg-2 p-4 font-mono text-sm text-fg">{children}</div>;
}
