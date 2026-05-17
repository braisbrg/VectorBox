import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

const isPublicRoute = createRouteMatcher([
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

    // Si está logueado e intenta ir a /login o /register, redirigir a home
    if (userId && (request.nextUrl.pathname.startsWith('/login') ||
        request.nextUrl.pathname.startsWith('/register'))) {
        // Allow /login?migrate=true to proceed (onboarding migration needs the login page)
        if (request.nextUrl.pathname.startsWith('/login') && request.nextUrl.searchParams.get('migrate') === 'true') {
            // Don't redirect - let the login page handle migration
        } else {
            return Response.redirect(new URL('/', request.url));
        }
    }

    // Proteger rutas no públicas - redirigir a nuestro /login chooser
    // (no a la página hosted de Clerk) para que el guest pueda elegir
    // "Rate Films" sin pasar por sign-in.
    if (!isPublicRoute(request) && !userId) {
        return NextResponse.redirect(new URL('/login', request.url));
    }
});

export const config = {
    matcher: [
        "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
        "/(api|trpc)(.*)",
    ],
};