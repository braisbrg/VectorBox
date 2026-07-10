import Link from "next/link";
import { Metadata } from "next";

export const metadata: Metadata = {
    title: "404: Signal Lost | VectorBox",
    description: "The page you are looking for has been lost in the matrix.",
};

// 404 — states gallery 03 "route not found"
export default function NotFound() {
    return (
        <main className="flex min-h-screen flex-col items-center justify-center bg-bg p-6">
            <div className="w-full max-w-md border border-border-2 bg-bg-2 p-10 text-center">
                <p className="font-display text-7xl leading-none text-primary">404</p>
                <p className="mt-3 font-mono text-[11px] uppercase tracking-widest text-fg-3">
                    route_not_in_vector_space
                </p>
                <Link
                    href="/feed"
                    className="mt-6 inline-block border border-border-2 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.1em] text-fg-2 transition-colors hover:border-primary hover:text-primary"
                >
                    ← back to feed
                </Link>
            </div>
        </main>
    );
}
