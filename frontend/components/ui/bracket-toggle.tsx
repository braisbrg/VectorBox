"use client";

import { cn } from "@/lib/utils";

interface BracketToggleProps {
    checked: boolean;
    onChange: (next: boolean) => void;
    /** Accessible label for the switch (the visible text is the ON/OFF state). */
    label?: string;
    disabled?: boolean;
    className?: string;
    id?: string;
}

/**
 * ACID bracket switch (Toggle Options · Option A — the locked design decision).
 * Reads as a terminal command: `● ON` filled acid / `OFF` hollow. The state
 * word is the affordance, so on/off never relies on colour alone.
 */
export function BracketToggle({ checked, onChange, label, disabled, className, id }: BracketToggleProps) {
    return (
        <button
            type="button"
            role="switch"
            aria-checked={checked}
            aria-label={label}
            id={id}
            disabled={disabled}
            onClick={() => !disabled && onChange(!checked)}
            className={cn(
                "inline-flex min-h-[44px] min-w-[64px] items-center justify-center px-3 py-2",
                "border font-display text-xs uppercase tracking-wide transition-colors",
                "disabled:cursor-not-allowed disabled:opacity-40",
                checked
                    ? "border-primary bg-primary text-primary-ink shadow-[0_0_12px_-2px_var(--primary)]"
                    : "border-border-2 bg-bg text-fg-3 hover:border-fg-3 hover:text-fg-2",
                className
            )}
        >
            {checked ? "● ON" : "OFF"}
        </button>
    );
}
