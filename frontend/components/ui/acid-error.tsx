"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";

interface AcidErrorProps {
    message?: string;
    onRetry?: () => void;
    className?: string;
}

export function AcidError({ message = "SYSTEM_FAILURE", onRetry, className }: AcidErrorProps) {
    return (
        <div
            role="alert"
            aria-live="assertive"
            aria-atomic="true"
            className={cn(
                "relative flex min-h-[400px] w-full flex-col items-center justify-center p-8",
                "border border-border-2 bg-bg-2 font-mono text-primary",
                className
            )}
        >
            <AlertTriangle className="mb-6 size-16" strokeWidth={1.5} />

            <h2 className="mb-2 font-display text-2xl uppercase tracking-widest">{message}</h2>
            <p className="mb-8 max-w-md text-center text-sm text-fg-3">
                CRITICAL_ERROR: the system reached an unrecoverable state. Protocol initiated:
                MANUAL_RESET_REQUIRED.
            </p>

            {onRetry && (
                <button
                    onClick={onRetry}
                    aria-label="Retry loading"
                    className="inline-flex min-h-[44px] items-center gap-2 border border-primary bg-transparent px-6 py-3 font-bold uppercase tracking-wider text-primary shadow-[2px_2px_0_0_var(--primary)] transition-transform hover:-translate-x-px hover:-translate-y-px active:translate-x-0.5 active:translate-y-0.5"
                >
                    <RefreshCw className="size-4" />
                    RELOAD_SYSTEM
                </button>
            )}

            <div className="absolute left-2 top-2 text-[10px] text-fg-3 opacity-60">ERR_CODE: 0xDEADBEEF</div>
            <div className="absolute bottom-2 right-2 text-[10px] text-fg-3 opacity-60">SYS_HALT</div>
        </div>
    );
}
