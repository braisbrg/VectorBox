import { Suspense } from 'react';
import { Dashboard } from '@/components/dashboard';

export const metadata = {
    title: 'VectorBox - Home',
};

// SSR feed prefetch was removed on 2026-05-17. It depended on a legacy
// `vectorbox_token` cookie that the Clerk-only auth migration eliminated
// — so `userId` stayed undefined, `getFeedServerSide` never ran, and the
// Dashboard always hydrated from the client. The dead block was kept for
// months as commented-out "TODO Sprint 1" debt; with Clerk session
// retrieval working server-side via @clerk/nextjs/server, the right path
// when we want SSR prefetch back is `auth()` from there, not parsing
// cookies by hand.
export default function HomePage() {
    return (
        <main className="min-h-screen bg-zinc-950 text-primary">
            <Suspense fallback={null}>
                <Dashboard initialFeedData={null} />
            </Suspense>
        </main>
    );
}
