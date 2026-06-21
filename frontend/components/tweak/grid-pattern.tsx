"use client";

import { useId } from "react";
import { cn } from "@/lib/utils";

interface GridPatternProps {
    className?: string;
    width?: number;
    height?: number;
    strokeColor?: string;
    strokeWidth?: number;
    fadeStart?: string;
    fadeEnd?: string;
}

/**
 * GridPattern - A background SVG grid that fades out at the edges.
 * Removes the "flat black" emptiness without adding noise.
 * 
 * @usage <GridPattern className="absolute inset-0 -z-10 opacity-20" />
 */
export function GridPattern({
    className,
    width = 40,
    height = 40,
    strokeColor = "rgba(204, 255, 0, 0.1)",
    strokeWidth = 1,
    fadeStart = "0%",
    fadeEnd = "80%",
}: GridPatternProps) {
    const id = useId();
    const patternId = `grid-pattern${id}`;
    const maskId = `grid-mask${id}`;

    return (
        <svg
            aria-hidden="true"
            className={cn(
                "pointer-events-none absolute inset-0 h-full w-full",
                className
            )}
        >
            <defs>
                {/* Grid Pattern */}
                <pattern
                    id={patternId}
                    width={width}
                    height={height}
                    patternUnits="userSpaceOnUse"
                >
                    <path
                        d={`M ${width} 0 L 0 0 0 ${height}`}
                        fill="none"
                        stroke={strokeColor}
                        strokeWidth={strokeWidth}
                    />
                </pattern>

                {/* Radial Fade Mask */}
                <radialGradient id={maskId}>
                    <stop offset={fadeStart} stopColor="white" />
                    <stop offset={fadeEnd} stopColor="black" />
                </radialGradient>

                <mask id={`${maskId}-mask`}>
                    <rect width="100%" height="100%" fill={`url(#${maskId})`} />
                </mask>
            </defs>

            <rect
                width="100%"
                height="100%"
                fill={`url(#${patternId})`}
                mask={`url(#${maskId}-mask)`}
            />
        </svg>
    );
}
