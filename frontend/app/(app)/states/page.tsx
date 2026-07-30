"use client";

// Canonical states gallery (handoff /states): the 8 system states rendered
// with production components so design and reality can't drift.

import { AcidError } from "@/components/ui/acid-error";
import { Skeleton } from "@/components/ui/skeleton";

function Frame({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <div className="border border-border-2 bg-bg-2">
            <div className="border-b border-border-2 bg-bg-3 px-3 py-1.5 font-display text-[9px] uppercase tracking-[0.15em] text-fg-3">
                {label}
            </div>
            <div className="p-4">{children}</div>
        </div>
    );
}

const Empty = ({ glyph, title, note, cta }: { glyph: string; title: string; note: string; cta: string }) => (
    <div className="border border-dashed border-border-2 p-8 text-center">
        <p className="font-display text-3xl text-primary">{glyph}</p>
        <p className="mt-3 font-display text-lg uppercase text-fg">{title}</p>
        <p className="mt-1.5 font-mono text-[11px] text-fg-3">{note}</p>
        <button className="mt-4 border border-primary px-4 py-2 font-mono text-[10px] uppercase tracking-[0.1em] text-primary transition-colors hover:bg-primary hover:text-primary-ink">
            {cta}
        </button>
    </div>
);

export default function StatesPage() {
    return (
        <div className="space-y-5 pt-6">
            <div>
                <h1 className="font-display text-2xl uppercase tracking-[-0.02em] text-fg">states</h1>
                <p className="tiny mt-1">canonical system states · rendered with production components</p>
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <Frame label="01 · loading — vector compute">
                    <div className="space-y-3" role="status" aria-label="Loading">
                        <Skeleton className="h-5 w-40" />
                        <div className="flex gap-3 overflow-hidden">
                            {[1, 2, 3, 4].map((i) => (
                                <Skeleton key={i} className="h-[200px] w-[130px] shrink-0" />
                            ))}
                        </div>
                        <p className="font-mono text-[10px] uppercase tracking-widest text-fg-3">computing_taste_vector…</p>
                    </div>
                </Frame>

                <Frame label="02 · error — generic (with trace)">
                    <AcidError message="SYSTEM_FAILURE" onRetry={() => {}} className="min-h-[240px]" />
                </Frame>

                <Frame label="03 · 404 — route not found">
                    <div className="p-6 text-center">
                        <p className="font-display text-6xl text-primary">404</p>
                        <p className="mt-2 font-mono text-[11px] uppercase tracking-widest text-fg-3">
                            route_not_in_vector_space
                        </p>
                        <button className="mt-4 border border-border-2 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.1em] text-fg-2 hover:border-primary hover:text-primary">
                            ← back to feed
                        </button>
                    </div>
                </Frame>

                <Frame label="04 · empty — no ratings yet">
                    <Empty glyph="◐" title="no signal yet" note="rate a few films to place yourself in the space." cta="start rating →" />
                </Frame>

                <Frame label="05 · empty — no provider matches">
                    <Empty glyph="▤" title="nothing on your providers" note="widen the provider filter or clear it in the rail." cta="clear filters" />
                </Frame>

                <Frame label="06 · empty — no search results">
                    <Empty glyph="⌕" title="no_results_found" note="try a looser query — drop a filter chip or two." cta="edit query" />
                </Frame>

                <Frame label="07 · degraded — cached feed">
                    <div className="border border-warn/50 bg-bg-3 p-4">
                        <p className="font-display text-[10px] uppercase tracking-[0.15em] text-warn">degraded · serving cache</p>
                        <p className="mt-1.5 font-mono text-[11px] leading-relaxed text-fg-2">
                            the engine is recomputing your clusters. this feed is from {"~"}23m ago and refreshes automatically.
                        </p>
                    </div>
                </Frame>

                <Frame label="08 · import error">
                    <div className="border border-danger/50 bg-bg-3 p-4">
                        <p className="font-display text-[10px] uppercase tracking-[0.15em] text-danger">import_failed</p>
                        <p className="mt-1.5 font-mono text-[11px] leading-relaxed text-fg-2">
                            the ZIP didn&apos;t parse — make sure it&apos;s the unmodified letterboxd export. nothing was replaced.
                        </p>
                        <button className="mt-3 border border-border-2 px-3 py-1.5 font-mono text-[10px] uppercase text-fg-2 hover:border-primary hover:text-primary">
                            retry upload
                        </button>
                    </div>
                </Frame>
            </div>
        </div>
    );
}
