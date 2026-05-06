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

    // TODO Sprint 1 (Clerk): reactivar este guard cuando Clerk gestione la
    // sesión server-side. Actualmente desactivado porque el middleware.ts de
    // Next.js aún no existe y la app usa localStorage como fallback de auth
    // en cliente. Con localStorage activo, borrar solo la cookie no cierra
    // la sesión en Chromium — el Dashboard sigue renderizando.
    // DEUDA: auth dual (cookie + localStorage) es un riesgo de seguridad.
    // Al migrar a Clerk, localStorage.vectorbox_user debe eliminarse.
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
