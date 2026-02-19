"use client";

import { useEffect } from "react";

/**
 * Global Error Boundary - Acid Design
 * Catches runtime errors and provides reset functionality
 */
export default function Error({
    error,
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}) {
    useEffect(() => {
        // Log error to monitoring service
        console.error("Application error:", error);
    }, [error]);

    return (
        <main className="min-h-screen bg-black flex flex-col items-center justify-center p-6">
            {/* Glitch Effect Container */}
            <div className="text-center space-y-8 max-w-md">
                {/* Error Code */}
                <div className="relative">
                    <h1 className="text-[120px] md:text-[180px] font-black font-space text-transparent leading-none"
                        style={{
                            WebkitTextStroke: "2px hsl(var(--primary))",
                        }}
                    >
                        ERR
                    </h1>
                    <div className="absolute inset-0 flex items-center justify-center">
                        <span className="text-primary font-mono text-sm uppercase tracking-widest animate-pulse">
                            System Failure
                        </span>
                    </div>
                </div>

                {/* Message */}
                <div className="space-y-2">
                    <p className="text-white/80 font-mono text-lg uppercase tracking-wide">
                        Something went wrong
                    </p>
                    <p className="text-white/40 font-mono text-xs">
                        Reconnecting to the matrix...
                    </p>
                </div>

                {/* Action Buttons */}
                <div className="flex flex-col sm:flex-row gap-4 justify-center pt-4">
                    <button
                        onClick={reset}
                        className="px-8 py-4 bg-primary text-black font-black font-mono uppercase tracking-wider hover:bg-white transition-colors border-2 border-primary"
                        aria-label="Try again"
                    >
                        Try Again
                    </button>
                    <a
                        href="/"
                        className="px-8 py-4 bg-transparent text-primary font-mono uppercase tracking-wider hover:bg-primary/10 transition-colors border-2 border-primary text-center"
                        aria-label="Return to home page"
                    >
                        Go Home
                    </a>
                </div>

                {/* Decorative Elements */}
                <div className="pt-8 flex justify-center gap-2">
                    {[...Array(5)].map((_, i) => (
                        <div
                            key={i}
                            className="w-2 h-2 bg-primary/30"
                            style={{
                                animation: `pulse 1s ease-in-out ${i * 0.2}s infinite`,
                            }}
                        />
                    ))}
                </div>
            </div>
        </main>
    );
}
