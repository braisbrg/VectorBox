"use client";

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

interface BorderBeamProps {
    className?: string;
    size?: number;
    duration?: number;
    borderWidth?: number;
    colorFrom?: string;
    colorTo?: string;
    delay?: number;
}

/**
 * BorderBeam - A moving gradient border effect for containers.
 * Creates a "living AI terminal" look with a beam that travels around the container.
 * 
 * @usage Wrap your container: <div className="relative"><BorderBeam /><YourContent /></div>
 */
export function BorderBeam({
    className,
    size = 200,
    duration = 12,
    borderWidth = 1.5,
    colorFrom = "hsl(var(--primary))",
    colorTo = "transparent",
    delay = 0,
}: BorderBeamProps) {
    return (
        <div
            style={
                {
                    "--size": size,
                    "--duration": duration,
                    "--border-width": borderWidth,
                    "--color-from": colorFrom,
                    "--color-to": colorTo,
                    "--delay": `-${delay}s`,
                } as React.CSSProperties
            }
            className={cn(
                "pointer-events-none absolute inset-0 rounded-[inherit]",
                // Mask to show only the border area
                "[mask-clip:padding-box,border-box]",
                "[mask-composite:intersect]",
                "[mask-image:linear-gradient(transparent,transparent),linear-gradient(#fff,#fff)]",
                // Border setup
                "border-[calc(var(--border-width)*1px)] border-transparent",
                // The animated gradient
                "[background:linear-gradient(to_right,var(--color-from),var(--color-to),transparent,transparent)_border-box]",
                "bg-[length:calc(var(--size)*1px)_100%]",
                // Animation
                "animate-border-beam",
                className
            )}
        />
    );
}

// Add this to your globals.css or tailwind.config.ts:
// @keyframes border-beam {
//   0% { background-position: 0% 0%; }
//   100% { background-position: 200% 0%; }
// }
// .animate-border-beam {
//   animation: border-beam calc(var(--duration)*1s) linear infinite;
//   animation-delay: var(--delay);
// }
