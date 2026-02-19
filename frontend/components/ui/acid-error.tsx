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
        <div className={cn(
            "flex flex-col items-center justify-center p-8 min-h-[400px] w-full",
            "bg-black border border-[#CCFF00]/20 rounded-xl",
            "font-[family-name:var(--font-mono-acid)] text-[#CCFF00]",
            "relative overflow-hidden",
            className
        )}>
            {/* Background Glitch Effect */}
            <div className="absolute inset-0 bg-[url('/noise.svg')] opacity-10 pointer-events-none" />
            <div className="absolute inset-0 bg-gradient-to-b from-transparent via-[#CCFF00]/5 to-transparent pointer-events-none animate-pulse" />

            {/* Icon */}
            <div className="mb-6 relative">
                <div className="absolute inset-0 bg-[#CCFF00] blur-xl opacity-20 animate-pulse" />
                <AlertTriangle className="w-16 h-16 relative z-10" strokeWidth={1.5} />
            </div>

            {/* Text */}
            <h2 className="text-2xl font-bold tracking-widest mb-2 uppercase animate-glitch">
                {message}
            </h2>
            <p className="text-sm text-[#CCFF00]/60 mb-8 max-w-md text-center">
                CRITICAL_ERROR: The system encountered an unrecoverable state.
                Protocol initiated: MANUAL_RESET_REQUIRED.
            </p>

            {/* Action */}
            {onRetry && (
                <button
                    onClick={onRetry}
                    className="group relative px-8 py-3 bg-transparent border border-[#CCFF00] overflow-hidden transition-all hover:bg-[#CCFF00]/10"
                >
                    <div className="absolute inset-0 bg-[#CCFF00]/20 translate-y-full group-hover:translate-y-0 transition-transform duration-300" />
                    <span className="relative flex items-center gap-2 font-bold tracking-wider">
                        <RefreshCw className="w-4 h-4 group-hover:animate-spin" />
                        RELOAD_SYSTEM
                    </span>
                </button>
            )}

            {/* Decoration */}
            <div className="absolute top-2 left-2 text-[10px] opacity-40">
                ERR_CODE: 0xDEADBEEF
            </div>
            <div className="absolute bottom-2 right-2 text-[10px] opacity-40">
                SYS_HALT
            </div>
        </div>
    );
}
