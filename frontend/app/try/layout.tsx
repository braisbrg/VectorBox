import Link from "next/link";

// Public /try/* shell (handoff pre-login route map): guest features share a
// topbar with a persistent "create account →" CTA. No auth, no app shell.
export default function TryLayout({ children }: { children: React.ReactNode }) {
    return (
        <div className="min-h-screen bg-bg text-fg">
            <header className="flex h-[54px] items-center justify-between border-b border-border-2 bg-bg px-5">
                <Link href="/" className="font-display text-base uppercase tracking-tight">
                    <span className="text-fg">VECTOR</span>
                    <span className="ml-0.5 bg-primary px-1.5 py-0.5 text-primary-ink">BOX</span>
                </Link>
                <div className="flex items-center gap-3">
                    <span className="hidden font-mono text-[10px] uppercase tracking-[0.1em] text-fg-3 sm:inline">
                        ◌ guest mode · rate-limited
                    </span>
                    <Link
                        href="/login"
                        className="border border-border-2 px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.1em] text-fg-2 transition-colors hover:border-primary hover:text-primary"
                    >
                        sign in
                    </Link>
                    <Link
                        href="/register"
                        className="bg-primary px-3 py-1.5 font-display text-[10px] font-bold uppercase tracking-[0.1em] text-primary-ink"
                    >
                        create account →
                    </Link>
                </div>
            </header>
            <main className="container mx-auto px-4 pb-16">{children}</main>
        </div>
    );
}
