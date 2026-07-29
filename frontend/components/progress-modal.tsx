"use client";

// Task-progress modal — ACID re-skin. Polling lives in lib/use-task-progress
// (shared with the import wizard's enrich step). External API unchanged.

import { m, AnimatePresence } from "framer-motion";
import { Loader2 } from "lucide-react";
import { useTaskProgress } from "@/lib/use-task-progress";

interface ProgressModalProps {
    taskId: string | null;
    onComplete: () => void;
    onError?: (error: string) => void;
}

export function ProgressModal({ taskId, onComplete, onError }: ProgressModalProps) {
    const { status, error } = useTaskProgress(taskId, { onComplete, onError });

    if (!taskId) return null;

    return (
        <AnimatePresence>
            <m.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 z-50 flex items-center justify-center bg-black/80"
            >
                <m.div
                    initial={{ scale: 0.96, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    exit={{ scale: 0.96, opacity: 0 }}
                    className="mx-4 w-full max-w-md border-2 border-primary bg-bg p-6 font-mono shadow-acid-primary"
                >
                    {/* Header */}
                    <div className="mb-5 flex items-center gap-3">
                        {status?.status === "completed" ? (
                            <span className="font-display text-2xl text-primary">✓</span>
                        ) : error ? (
                            <span className="font-display text-2xl text-danger">✕</span>
                        ) : (
                            <Loader2 className="size-6 animate-spin text-primary" />
                        )}
                        <div>
                            <h2 className="font-display text-lg uppercase tracking-tight text-fg">
                                {status?.status === "completed" ? "complete" : error ? "error" : "processing…"}
                            </h2>
                            <p className="font-mono text-xs text-fg-3">{error || status?.step || "initializing…"}</p>
                        </div>
                    </div>

                    {/* Progress bar */}
                    <div className="relative mb-3 h-2 overflow-hidden border border-border-2 bg-bg-3">
                        <m.div
                            className="absolute inset-y-0 left-0 bg-primary"
                            initial={{ width: 0 }}
                            animate={{ width: `${status?.progress || 0}%` }}
                            transition={{ duration: 0.3, ease: "easeOut" }}
                        />
                    </div>

                    <div className="flex justify-between font-mono text-xs">
                        <span className="uppercase tracking-[0.1em] text-fg-3">progress</span>
                        <span className="font-display font-bold text-primary">{status?.progress || 0}%</span>
                    </div>

                    {error && (
                        <button
                            onClick={() => window.location.reload()}
                            className="mt-5 w-full border border-danger/60 px-4 py-2 font-mono text-xs uppercase tracking-[0.08em] text-danger transition-colors hover:bg-danger hover:text-bg"
                        >
                            retry
                        </button>
                    )}
                </m.div>
            </m.div>
        </AnimatePresence>
    );
}
