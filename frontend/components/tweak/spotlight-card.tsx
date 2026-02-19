"use client";

import { useRef, useState, useCallback } from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

interface SpotlightCardProps {
    children: React.ReactNode;
    className?: string;
    spotlightColor?: string;
    spotlightSize?: number;
}

/**
 * SpotlightCard - A card with a radial gradient glow that follows the mouse cursor.
 * Creates a premium "hover discovery" effect.
 * 
 * Performance: Geometry is cached on mouseEnter, updates throttled via rAF.
 * 
 * @usage <SpotlightCard><YourCardContent /></SpotlightCard>
 */
export function SpotlightCard({
    children,
    className,
    spotlightColor = "rgba(204, 255, 0, 0.08)",
    spotlightSize = 350,
}: SpotlightCardProps) {
    const containerRef = useRef<HTMLDivElement>(null);
    const cachedRectRef = useRef<DOMRect | null>(null);
    const rafIdRef = useRef<number | null>(null);
    const [position, setPosition] = useState({ x: 0, y: 0 });
    const [isHovered, setIsHovered] = useState(false);

    // Cache geometry on enter - avoids layout thrashing during move
    const handleMouseEnter = useCallback(() => {
        if (containerRef.current) {
            cachedRectRef.current = containerRef.current.getBoundingClientRect();
        }
        setIsHovered(true);
    }, []);

    const handleMouseLeave = useCallback(() => {
        setIsHovered(false);
        cachedRectRef.current = null;
        if (rafIdRef.current) {
            cancelAnimationFrame(rafIdRef.current);
            rafIdRef.current = null;
        }
    }, []);

    // Throttled via requestAnimationFrame (60fps max)
    const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
        if (rafIdRef.current) return; // Skip if already scheduled

        rafIdRef.current = requestAnimationFrame(() => {
            const rect = cachedRectRef.current;
            if (rect) {
                setPosition({
                    x: e.clientX - rect.left,
                    y: e.clientY - rect.top,
                });
            }
            rafIdRef.current = null;
        });
    }, []);

    return (
        <motion.div
            ref={containerRef}
            onMouseMove={handleMouseMove}
            onMouseEnter={handleMouseEnter}
            onMouseLeave={handleMouseLeave}
            whileHover={{ scale: 1.02 }}
            transition={{ type: "spring", stiffness: 400, damping: 25 }}
            className={cn(
                "relative overflow-hidden p-4", // Defensive padding
                className
            )}
        >
            {/* Spotlight Effect */}
            <div
                className="pointer-events-none absolute inset-0 z-10 transition-opacity duration-300"
                style={{
                    opacity: isHovered ? 1 : 0,
                    background: `radial-gradient(${spotlightSize}px circle at ${position.x}px ${position.y}px, ${spotlightColor}, transparent 60%)`,
                }}
            />
            {children}
        </motion.div>
    );
}

