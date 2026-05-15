"use client";

import { useState, useEffect, useCallback } from "react";
import { m, AnimatePresence } from "framer-motion";
import { Loader2, CheckCircle, XCircle } from "lucide-react";
import { getTaskStatus, TaskStatus } from "@/lib/api";

interface ProgressModalProps {
    taskId: string | null;
    onComplete: () => void;
    onError?: (error: string) => void;
}

/**
 * ProgressModal - v1.1 Component
 * Displays a modal that polls task progress during upload/sync operations.
 * Shows a progress bar with step descriptions.
 * Auto-closes and calls onComplete when progress reaches 100%.
 */
export function ProgressModal({ taskId, onComplete, onError }: ProgressModalProps) {
    const [status, setStatus] = useState<TaskStatus | null>(null);
    const [error, setError] = useState<string | null>(null);

    const pollStatus = useCallback(async () => {
        if (!taskId) return;

        try {
            const result = await getTaskStatus(taskId);
            setStatus(result);

            if (result.status === "completed") {
                // Delay slightly before calling onComplete for visual feedback
                setTimeout(() => {
                    onComplete();
                }, 1000);
            } else if (result.status === "failed") {
                setError(result.step || "Task failed");
                onError?.(result.step || "Task failed");
            }
        } catch (err) {
            console.error("Error polling task status:", err);
            setError("Failed to get task status");
        }
    }, [taskId, onComplete, onError]);

    useEffect(() => {
        if (!taskId) return;

        // Initial poll
        pollStatus();

        // Poll every 500ms
        const interval = setInterval(() => {
            if (status?.status === "completed" || status?.status === "failed") {
                clearInterval(interval);
                return;
            }
            pollStatus();
        }, 500);

        return () => clearInterval(interval);
    }, [taskId, pollStatus, status?.status]);

    if (!taskId) return null;

    return (
        <AnimatePresence>
            <m.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 z-[9999] flex items-center justify-center bg-zinc-950/80 backdrop-blur-sm"
            >
                <m.div
                    initial={{ scale: 0.9, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    exit={{ scale: 0.9, opacity: 0 }}
                    className="w-full max-w-md mx-4 p-6 rounded-2xl bg-zinc-900 border border-zinc-700 shadow-2xl"
                >
                    {/* Header */}
                    <div className="flex items-center gap-3 mb-6">
                        {status?.status === "completed" ? (
                            <CheckCircle className="size-8 text-lime-400" />
                        ) : error ? (
                            <XCircle className="size-8 text-red-400" />
                        ) : (
                            <Loader2 className="size-8 text-lime-400 animate-spin" />
                        )}
                        <div>
                            <h2 className="text-xl font-semibold text-white">
                                {status?.status === "completed"
                                    ? "Complete!"
                                    : error
                                        ? "Error"
                                        : "Processing..."}
                            </h2>
                            <p className="text-sm text-zinc-400">
                                {error || status?.step || "Initializing..."}
                            </p>
                        </div>
                    </div>

                    {/* Progress Bar */}
                    <div className="relative h-3 bg-zinc-800 rounded-full overflow-hidden mb-4">
                        <m.div
                            className="absolute inset-y-0 left-0 bg-gradient-to-r from-lime-500 to-lime-400 rounded-full"
                            initial={{ width: 0 }}
                            animate={{ width: `${status?.progress || 0}%` }}
                            transition={{ duration: 0.3, ease: "easeOut" }}
                        />
                    </div>

                    {/* Progress Percentage */}
                    <div className="flex justify-between text-sm">
                        <span className="text-zinc-400">Progress</span>
                        <span className="text-lime-400 font-mono font-bold">
                            {status?.progress || 0}%
                        </span>
                    </div>

                    {/* Error Retry Button */}
                    {error && (
                        <button
                            onClick={() => window.location.reload()}
                            className="w-full mt-6 py-2 px-4 bg-red-500/20 hover:bg-red-500/30 text-red-400 rounded-lg border border-red-500/30 transition-colors"
                        >
                            Retry
                        </button>
                    )}
                </m.div>
            </m.div>
        </AnimatePresence>
    );
}
