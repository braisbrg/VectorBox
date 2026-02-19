import Link from "next/link";

/**
 * Custom 404 Page - Acid Design
 * "Glitch in the Matrix" theme
 */
import { Metadata } from "next";

export const metadata: Metadata = {
    title: "404: Signal Lost | VectorBox",
    description: "The page you are looking for has been lost in the matrix.",
};

export default function NotFound() {
    return (
        <main className="min-h-screen bg-black flex flex-col items-center justify-center p-6">
            {/* Glitch Effect Container */}
            <div className="text-center space-y-8 max-w-md">
                {/* 404 Display */}
                <div className="relative">
                    <h1 className="text-[120px] md:text-[180px] font-black font-space text-transparent leading-none"
                        style={{
                            WebkitTextStroke: "2px hsl(var(--primary))",
                        }}
                    >
                        404
                    </h1>
                    <div className="absolute inset-0 flex items-center justify-center">
                        <span className="text-primary font-mono text-sm uppercase tracking-widest animate-pulse">
                            Signal Lost
                        </span>
                    </div>
                </div>

                {/* Message */}
                <div className="space-y-2">
                    <p className="text-white/80 font-mono text-lg uppercase tracking-wide">
                        Glitch in the Matrix
                    </p>
                    <p className="text-white/40 font-mono text-xs">
                        The page you&apos;re looking for doesn&apos;t exist in this dimension.
                    </p>
                </div>

                {/* Action Button */}
                <div className="pt-4">
                    <Link
                        href="/"
                        className="inline-block px-8 py-4 bg-primary text-black font-black font-mono uppercase tracking-wider hover:bg-white transition-colors border-2 border-primary"
                        aria-label="Return to home page"
                    >
                        Return Home
                    </Link>
                </div>

                {/* Decorative Grid Lines */}
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
