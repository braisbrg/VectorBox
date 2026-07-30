"use client";

import { m, AnimatePresence, useReducedMotion } from "framer-motion";

interface BottomSheetProps {
    open: boolean;
    onClose: () => void;
    ariaLabel?: string;
    children: React.ReactNode;
}

/**
 * Mobile bottom sheet (handoff `.sheet` pattern): backdrop fade + spring
 * slide-up + grab handle. `lg:hidden` — mobile-only by construction.
 * Consumers: MobileInspector, feed filter sheet, watchlist filter sheet.
 */
export function BottomSheet({ open, onClose, ariaLabel, children }: BottomSheetProps) {
    // Global CSS only zeroes CSS animations — framer springs need an explicit gate.
    const reduceMotion = useReducedMotion();
    const spring = reduceMotion ? { duration: 0 } : { type: "spring" as const, damping: 28, stiffness: 240 };

    return (
        <AnimatePresence>
            {open && (
                <>
                    <m.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={reduceMotion ? { duration: 0 } : undefined}
                        onClick={onClose}
                        className="fixed inset-0 z-50 bg-black/70 lg:hidden"
                    />
                    <m.div
                        initial={{ y: "100%" }}
                        animate={{ y: 0 }}
                        exit={{ y: "100%" }}
                        transition={spring}
                        className="fixed inset-x-0 bottom-0 z-50 flex max-h-[90vh] flex-col border-t border-border-2 bg-bg-2 font-mono lg:hidden"
                        role="dialog"
                        aria-modal="true"
                        aria-label={ariaLabel}
                    >
                        <button
                            onClick={onClose}
                            aria-label="Close"
                            className="mx-auto my-3 h-1.5 w-12 shrink-0 bg-border-2"
                        />
                        {children}
                    </m.div>
                </>
            )}
        </AnimatePresence>
    );
}
