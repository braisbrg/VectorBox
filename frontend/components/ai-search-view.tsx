"use client";

import { MagicSearch } from "./magic-search";

interface AISearchViewProps {
    userId: number;
}

export function AISearchView({ userId }: AISearchViewProps) {
    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="text-center mb-8">
                <h2 className="text-3xl font-bold mb-2">AI-Powered Search</h2>
                <p className="text-muted-foreground max-w-2xl mx-auto">
                    Describe what you want to watch in natural language.
                    Try queries like &ldquo;old gangster movie&rdquo;, &ldquo;90s hidden gem&rdquo;, or &ldquo;short anime under 90 minutes&rdquo;.
                </p>
            </div>

            {/* Search Component */}
            <MagicSearch userId={userId} />

            {/* Feature Highlights */}
            <div className="grid md:grid-cols-3 gap-4 mt-12 max-w-4xl mx-auto">
                <div className="bg-card border rounded-lg p-4">
                    <div className="text-2xl mb-2">🎯</div>
                    <h3 className="font-semibold mb-1">Semantic Understanding</h3>
                    <p className="text-sm text-muted-foreground">
                        Our AI understands context and expands your query with synonyms
                    </p>
                </div>
                <div className="bg-card border rounded-lg p-4">
                    <div className="text-2xl mb-2">📅</div>
                    <h3 className="font-semibold mb-1">Time Period Detection</h3>
                    <p className="text-sm text-muted-foreground">
                        Automatically interprets &ldquo;80s&rdquo;, &ldquo;modern&rdquo;, &ldquo;classic&rdquo; and more
                    </p>
                </div>
                <div className="bg-card border rounded-lg p-4">
                    <div className="text-2xl mb-2">💎</div>
                    <h3 className="font-semibold mb-1">Vibe Filtering</h3>
                    <p className="text-sm text-muted-foreground">
                        Find hidden gems, blockbusters, or anything in between
                    </p>
                </div>
            </div>
        </div>
    );
}
