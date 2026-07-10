"use client";

import { useEffect } from "react";
import Link from "next/link";
import { AcidError } from "@/components/ui/acid-error";

// Global error boundary — states gallery 02 "error — generic" (AcidError).
export default function Error({
    error,
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}) {
    useEffect(() => {
        console.error("Application error:", error);
    }, [error]);

    return (
        <main className="flex min-h-screen flex-col items-center justify-center bg-bg p-6">
            <div className="w-full max-w-xl">
                <AcidError message="SYSTEM_FAILURE" onRetry={reset} />
                <div className="mt-3 text-center">
                    <Link
                        href="/feed"
                        className="inline-block border border-border-2 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.1em] text-fg-2 transition-colors hover:border-primary hover:text-primary"
                    >
                        ← back to feed
                    </Link>
                </div>
            </div>
        </main>
    );
}
