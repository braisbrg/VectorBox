"use client";

import { MagicBox } from "@/components/magic-box";

// Dedicated magic-box page (handoff: modal ⌘K AND /mb page; mobile FAB lands here).
export default function MagicBoxPage() {
    return (
        <div className="mx-auto max-w-[760px] pt-6">
            <h1 className="mb-1 font-display text-2xl uppercase tracking-[-0.02em] text-fg">magic box</h1>
            <p className="tiny mb-4">natural language → parsed filters → ranked shelf</p>
            <MagicBox embedded />
        </div>
    );
}
