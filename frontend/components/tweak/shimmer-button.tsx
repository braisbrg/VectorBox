"use client";

import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

interface ShimmerButtonProps {
    children: React.ReactNode;
    className?: string;
    shimmerColor?: string;
    shimmerSize?: string;
    shimmerDuration?: string;
    disabled?: boolean;
    type?: "button" | "submit" | "reset";
    onClick?: () => void;
}

/**
 * ShimmerButton - A button with a subtle light reflection that sweeps across periodically.
 * Background Black, Text Neon Green, Shimmer White.
 * 
 * @usage <ShimmerButton>Click Me</ShimmerButton>
 */
export function ShimmerButton({
    children,
    className,
    shimmerColor = "rgba(255, 255, 255, 0.15)",
    shimmerSize = "50%",
    shimmerDuration = "2.5s",
    disabled = false,
    type = "button",
    onClick,
}: ShimmerButtonProps) {
    return (
        <motion.button
            type={type}
            disabled={disabled}
            onClick={onClick}
            whileHover={{ scale: disabled ? 1 : 1.02 }}
            whileTap={{ scale: disabled ? 1 : 0.98 }}
            className={cn(
                "group relative inline-flex items-center justify-center overflow-hidden",
                "bg-black text-primary border-2 border-primary",
                "font-bold uppercase tracking-wider",
                "transition-all duration-300",
                "hover:bg-primary hover:text-black",
                "disabled:opacity-50 disabled:cursor-not-allowed",
                className
            )}
            style={
                {
                    "--shimmer-color": shimmerColor,
                    "--shimmer-size": shimmerSize,
                    "--shimmer-duration": shimmerDuration,
                } as React.CSSProperties
            }
        >
            {/* Shimmer Effect */}
            <span
                className={cn(
                    "absolute inset-0 overflow-hidden",
                    "before:absolute before:inset-0",
                    "before:translate-x-[-100%]",
                    "before:animate-shimmer",
                    "before:bg-[linear-gradient(90deg,transparent,var(--shimmer-color),transparent)]",
                    "before:w-[var(--shimmer-size)]",
                    disabled && "before:animation-paused"
                )}
            />

            {/* Content */}
            <span className="relative z-10 flex items-center gap-2">
                {children}
            </span>
        </motion.button>
    );
}

// Add this to your globals.css or tailwind.config.ts:
// @keyframes shimmer {
//   0% { transform: translateX(-100%); }
//   100% { transform: translateX(300%); }
// }
// .animate-shimmer {
//   animation: shimmer var(--shimmer-duration) ease-in-out infinite;
// }
