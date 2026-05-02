import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

const isPublicRoute = createRouteMatcher([
    "/login(.*)",
    "/register(.*)",
    "/privacy(.*)",
    "/terms(.*)",
    "/onboarding(.*)"
]);

export default clerkMiddleware(async (auth, request) => {
    const { userId } = await auth();

    // Si está logueado e intenta ir a /login o /register, redirigir a home
    if (userId && (request.nextUrl.pathname.startsWith('/login') ||
        request.nextUrl.pathname.startsWith('/register'))) {
        // Allow /login?migrate=true to proceed (onboarding migration needs the login page)
        if (request.nextUrl.pathname.startsWith('/login') && request.nextUrl.searchParams.get('migrate') === 'true') {
            // Don't redirect — let the login page handle migration
        } else {
            return Response.redirect(new URL('/', request.url));
        }
    }

    // Proteger rutas no públicas
    if (!isPublicRoute(request)) {
        await auth.protect();
    }
});

export const config = {
    matcher: [
        "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
        "/(api|trpc)(.*)",
    ],
};