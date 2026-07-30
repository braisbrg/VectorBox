"use client";

import { Suspense } from "react";
import { MoreLikeThis } from "@/components/more-like-this";

// Guest similars — /similar/multi is public by design ("works for guests").
//
// Suspense because MoreLikeThis reads `?q=` (the landing's title field hands its
// text over) and useSearchParams opts the tree into client rendering; without a
// boundary the build refuses to prerender this route.
export default function TryMltPage() {
    return (
        <Suspense fallback={null}>
            <MoreLikeThis />
        </Suspense>
    );
}
