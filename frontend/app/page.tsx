import { cookies, headers } from 'next/headers';
import { redirect } from 'next/navigation';
import { Dashboard } from '@/components/dashboard';
import { getFeedServerSide, FeedResponse } from '@/lib/api';

export const metadata = {
    title: 'VectorBox - Home',
};

// Helper to decode JWT payload (without verification - just for reading user_id)
function decodeJwtPayload(token: string): { user_id?: number } | null {
    try {
        const base64Payload = token.split('.')[1];
        const payload = Buffer.from(base64Payload, 'base64').toString('utf8');
        return JSON.parse(payload);
    } catch {
        return null;
    }
}

export default async function HomePage() {
    // 1. Server-Side Auth Check
    const cookieStore = await cookies();
    const token = cookieStore.get('vectorbox_token');

    // 2. Client-Side Fallback (Relaxed Gatekeeping)
    // If no cookie, we don't redirect here. We let the client component (Dashboard) 
    // handle the auth check using localStorage if available.
    // if (!token) {
    //     redirect('/login');
    // }

    // 3. Extract user_id from JWT for prefetch
    let userId: number | undefined;
    if (token) {
        const payload = decodeJwtPayload(token.value);
        userId = payload?.user_id;
    }

    // 4. SSR Feed Prefetch (only if we have a valid user_id)
    let initialFeedData: FeedResponse | null = null;
    if (userId) {
        // Forward cookies to backend for auth
        const cookieHeader = cookieStore.getAll()
            .map(c => `${c.name}=${c.value}`)
            .join('; ');

        initialFeedData = await getFeedServerSide(
            "global",
            "ES",
            [],
            false,
            cookieHeader
        );
    }

    // 5. Render the App Shell with prefetched data
    return (
        <main className="min-h-screen bg-black text-primary">
            <Dashboard initialFeedData={initialFeedData} />
        </main>
    );
}
