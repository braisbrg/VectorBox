"use client";

import { MagicBox } from "@/components/magic-box";

// Guest magic box — /natural runs with optional auth (no watched-filter).
export default function TryMagicPage() {
    return (
        <div className="mx-auto max-w-[760px] pt-6">
            <h1 className="mb-1 font-display text-2xl uppercase tracking-[-0.02em] text-fg">magic box</h1>
            <p className="tiny mb-4">natural language → parsed filters → ranked shelf · no account needed</p>
            <MagicBox embedded />
        </div>
    );
}
