import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import { resolveLocale } from "@/lib/i18n";

const isPublicRoute = createRouteMatcher([
    "/",                  // landing split-screen (logged-out); logged-in → /feed (page.tsx)
    "/try(.*)",           // guest features from the landing (magic box, mlt) — no auth
    "/login(.*)",
    "/register(.*)",
    "/privacy(.*)",
    "/terms(.*)",
    "/onboarding(.*)",
    "/explore(.*)",
    // API routes are public AT THE MIDDLEWARE LAYER — each backend
    // endpoint decides its own auth via get_current_user /
    // get_anonymous_user / get_current_or_anonymous_user. The middleware
    // protecting /api/* broke the guest flow: POST /api/onboarding/init-
    // session (which needs NO auth, it CREATES the anon session) was
    // being redirected to /login → axios followed with POST → 405.
    // Keep route-level Clerk enforcement on PAGE routes only.
    "/api(.*)",
]);

export default clerkMiddleware(async (auth, request) => {
    const { userId } = await auth();

    // UI language: an explicit NEXT_LOCALE cookie (set by a prior visit or the
    // user's manual switch) always wins. Otherwise negotiate from the browser/OS
    // Accept-Language header — never geo-IP. We set it on BOTH the forwarded
    // request (so the server render + first paint already use the right locale,
    // no flash of English) and the response (so it persists for the browser).
    const detectedLocale = request.cookies.get("NEXT_LOCALE")
        ? null
        : resolveLocale(request.headers.get("accept-language"));
    if (detectedLocale) {
        request.cookies.set("NEXT_LOCALE", detectedLocale);
    }

    const finalize = (res: NextResponse) => {
        if (detectedLocale) {
            res.cookies.set("NEXT_LOCALE", detectedLocale, {
                path: "/",
                maxAge: 60 * 60 * 24 * 365, // 1 year
                sameSite: "lax",
            });
        }
        return res;
    };

    // Si está logueado e intenta ir a /login o /register, redirigir a home
    if (userId && (request.nextUrl.pathname.startsWith('/login') ||
        request.nextUrl.pathname.startsWith('/register'))) {
        // Allow /login?migrate=true to proceed (onboarding migration needs the login page)
        const isMigrate = request.nextUrl.pathname.startsWith('/login') &&
            request.nextUrl.searchParams.get('migrate') === 'true';
        if (!isMigrate) {
            return finalize(NextResponse.redirect(new URL('/feed', request.url)));
        }
    }

    // Proteger rutas no públicas - redirigir a nuestro /login chooser
    // (no a la página hosted de Clerk) para que el guest pueda elegir
    // "Rate Films" sin pasar por sign-in.
    if (!isPublicRoute(request) && !userId) {
        return finalize(NextResponse.redirect(new URL('/login', request.url)));
    }

    return finalize(
        detectedLocale
            ? NextResponse.next({ request: { headers: request.headers } })
            : NextResponse.next()
    );
});

export const config = {
    matcher: [
        "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
        "/(api|trpc)(.*)",
    ],
};