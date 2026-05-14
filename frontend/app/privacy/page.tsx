import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
    title: "Privacy Policy - VectorBox",
    description: "How VectorBox handles your data, cookies, and GDPR rights.",
};

export default function PrivacyPage() {
    return (
        <main className="min-h-screen bg-zinc-950 text-zinc-300">
            <div className="max-w-2xl mx-auto px-6 py-16">
                {/* Back link */}
                <Link
                    href="/"
                    className="text-[#CCFF00] hover:underline font-[var(--font-mono-acid)] text-sm inline-block mb-12"
                >
                    ← Back
                </Link>

                {/* Page title */}
                <h1 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-3xl sm:text-4xl uppercase tracking-tight mb-12">
                    Privacy Policy
                </h1>

                {/* Sections */}
                <div className="space-y-0">
                    {/* 1. DATA CONTROLLER */}
                    <section className="border-b border-zinc-800 pb-8 mb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            1. Data Controller
                        </h2>
                        <p className="text-sm text-zinc-400 leading-relaxed mb-4">
                            VectorBox is a personal, non-commercial project developed
                            and operated by <span className="text-zinc-300">VectorBox
                            Project</span>, based in Spain.
                        </p>
                        <p className="text-sm text-zinc-400 leading-relaxed mb-4">
                            VectorBox is an open-source, non-commercial project and
                            does not engage in any commercial activity. No goods or
                            services are sold through this platform.
                        </p>
                        <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg p-4 text-sm font-[var(--font-mono-acid)] text-zinc-300 space-y-1">
                            <p><span className="text-zinc-500">Entity:</span> VectorBox Project</p>
                            <p><span className="text-zinc-500">Country:</span> Spain</p>
                            <p><span className="text-zinc-500">Contact:</span> vectorbox.app@proton.me</p>
                        </div>
                    </section>

                    {/* 2. LEGAL BASIS FOR PROCESSING */}
                    <section className="border-b border-zinc-800 pb-8 mb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            2. Legal Basis for Processing
                        </h2>
                        <p className="text-sm text-zinc-400 leading-relaxed mb-3">
                            We process your personal data under the following legal
                            bases (Article 6 GDPR):
                        </p>
                        <ul className="text-sm text-zinc-400 leading-relaxed space-y-3 list-disc list-inside">
                            <li>
                                <strong className="text-zinc-300">Performance of a contract</strong> -
                                Processing your username, PIN, and preferences is
                                necessary to provide the VectorBox recommendation
                                service you have registered for.
                            </li>
                            <li>
                                <strong className="text-zinc-300">Explicit consent</strong> -
                                When you upload a Letterboxd CSV export or link your
                                RSS feed, you explicitly consent to VectorBox importing
                                and processing that data to generate recommendations.
                            </li>
                        </ul>
                    </section>

                    {/* 3. WHAT DATA WE COLLECT */}
                    <section className="border-b border-zinc-800 pb-8 mb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            3. What Data We Collect
                        </h2>
                        <ul className="text-sm text-zinc-400 leading-relaxed space-y-3 list-disc list-inside">
                            <li>
                                <strong className="text-zinc-300">Account identity</strong> -
                                Authentication is handled by Clerk (a third-party identity
                                provider). VectorBox stores your Clerk user ID and display
                                name. We do not store passwords - authentication credentials
                                are managed entirely by Clerk.
                            </li>
                            <li>
                                <strong className="text-zinc-300">Movie ratings</strong> -
                                Imported from your Letterboxd account via CSV export or RSS
                                feed. These ratings power your personalized recommendations.
                            </li>
                            <li>
                                <strong className="text-zinc-300">
                                    Streaming preferences
                                </strong>{" "}
                                - Your selected streaming platforms (e.g., Netflix, HBO Max)
                                used to filter recommendations by availability.
                            </li>
                            <li>
                                <strong className="text-zinc-300">Country code</strong> -
                                Used to determine regional streaming availability for
                                recommended titles.
                            </li>
                            <li>
                                <strong className="text-zinc-300">Minimum age</strong> -
                                VectorBox is intended for users aged 14 and over, in
                                accordance with Spanish data protection law (LOPDGDD
                                Art. 7). By registering, you confirm that you meet
                                this requirement.
                            </li>
                        </ul>
                        <p className="text-sm text-zinc-500 leading-relaxed mt-4 italic">
                            Note: Letterboxd CSV exports may contain an email field. This
                            field is discarded immediately in memory during import and is
                            never written to our database.
                        </p>
                    </section>

                    {/* 4. HOW WE USE YOUR DATA */}
                    <section className="border-b border-zinc-800 pb-8 mb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            4. How We Use Your Data
                        </h2>
                        <ul className="text-sm text-zinc-400 leading-relaxed space-y-3 list-disc list-inside">
                            <li>
                                Generate personalized movie recommendations using semantic
                                vector embeddings and K-Medoids clustering over your rating
                                history.
                            </li>
                            <li>
                                Improve feed relevance through our Trident recommendation
                                engine, which combines collaborative filtering, taste
                                profiling, and hidden gem discovery.
                            </li>
                            <li>
                                We do not sell, share, or transfer your personal data to any
                                third parties.
                            </li>
                        </ul>
                    </section>

                    {/* 5. COOKIES */}
                    <section className="border-b border-zinc-800 pb-8 mb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            5. Cookies
                        </h2>
                        <p className="text-sm text-zinc-400 leading-relaxed mb-3">
                            VectorBox uses a single, strictly necessary cookie:
                        </p>
                        <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg p-4 text-sm font-[var(--font-mono-acid)] text-zinc-300 space-y-1">
                            <p>
                                <span className="text-zinc-500">Name:</span> __session,
                                __clerk_* (set by Clerk)
                            </p>
                            <p>
                                <span className="text-zinc-500">Type:</span> HttpOnly,
                                SameSite=Lax, Secure (production)
                            </p>
                            <p>
                                <span className="text-zinc-500">Purpose:</span> Session
                                authentication via Clerk
                            </p>
                            <p>
                                <span className="text-zinc-500">Duration:</span> Session-based
                                (managed by Clerk)
                            </p>
                        </div>
                        <p className="text-sm text-zinc-500 leading-relaxed mt-4">
                            These cookies are used exclusively to maintain your authenticated
                            session via Clerk. They are not tracking or analytics cookies.
                            As strictly necessary cookies, they are exempt from consent
                            requirements under GDPR Article 5(3) of the ePrivacy Directive.
                        </p>
                    </section>

                    {/* 6. INTERNATIONAL DATA TRANSFERS */}
                    <section className="border-b border-zinc-800 pb-8 mb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            6. International Data Transfers
                        </h2>
                        <p className="text-sm text-zinc-400 leading-relaxed mb-3">
                            To provide the Natural Language Search feature, VectorBox
                            sends anonymised query data to the following third-party
                            AI providers located outside the European Economic Area:
                        </p>
                        <ul className="text-sm text-zinc-400 leading-relaxed space-y-2 list-disc list-inside">
                            <li>
                                <strong className="text-zinc-300">Clerk, Inc.</strong> (United States)
                                - Used for user authentication and session management.
                            </li>
                            <li>
                                <strong className="text-zinc-300">Groq, Inc.</strong> (United States)
                                - Used for LLM-based query parsing and cinematic description generation.
                            </li>
                            <li>
                                <strong className="text-zinc-300">OpenAI, LLC</strong> (United States)
                                - Used as fallback LLM provider.
                            </li>
                        </ul>
                        <p className="text-sm text-zinc-500 leading-relaxed mt-4">
                            These transfers are governed by Standard Contractual
                            Clauses (SCCs) as established by the European Commission,
                            and both providers participate in the EU-US Data Privacy
                            Framework. Only the text of your search query is
                            transmitted - no personal identifiers, usernames, or
                            rating history are shared with these providers.
                        </p>
                        <p className="text-sm text-zinc-500 leading-relaxed mt-3">
                            For details on how Clerk handles your authentication data,
                            see{" "}
                            <a
                                href="https://clerk.com/legal/privacy"
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-zinc-300 underline hover:text-[#CCFF00]"
                            >
                                Clerk&apos;s Privacy Policy
                            </a>.
                        </p>
                    </section>

                    {/* 7. DATA RETENTION */}
                    <section className="border-b border-zinc-800 pb-8 mb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            7. Data Retention
                        </h2>
                        <p className="text-sm text-zinc-400 leading-relaxed">
                            Your data is retained for as long as your account remains active.
                            You may request complete deletion of your account and all
                            associated data by contacting{" "}
                            <span className="text-zinc-300">vectorbox.app@proton.me</span>.
                            Upon receiving a valid deletion request, we will erase all
                            personal data within 30 days.
                        </p>
                    </section>

                    {/* 8. YOUR RIGHTS (GDPR) */}
                    <section className="border-b border-zinc-800 pb-8 mb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            8. Your Rights (GDPR)
                        </h2>
                        <p className="text-sm text-zinc-400 leading-relaxed mb-3">
                            Under the General Data Protection Regulation, you have the right
                            to:
                        </p>
                        <ul className="text-sm text-zinc-400 leading-relaxed space-y-2 list-disc list-inside">
                            <li>
                                <strong className="text-zinc-300">Access</strong> - Request a
                                copy of the personal data we hold about you.
                            </li>
                            <li>
                                <strong className="text-zinc-300">Rectification</strong> -
                                Correct any inaccurate personal data.
                            </li>
                            <li>
                                <strong className="text-zinc-300">Erasure</strong> - Request
                                deletion of your personal data.
                            </li>
                            <li>
                                <strong className="text-zinc-300">Portability</strong> -
                                Receive your data in a structured, machine-readable format.
                            </li>
                            <li>
                                <strong className="text-zinc-300">Restriction</strong> -
                                Request that we restrict the processing of your personal
                                data under certain circumstances.
                            </li>
                            <li>
                                <strong className="text-zinc-300">Objection</strong> - Object
                                to the processing of your personal data.
                            </li>
                        </ul>
                        <p className="text-sm text-zinc-500 leading-relaxed mt-4">
                            Supervisory authority: Agencia Española de Protección de Datos
                            (AEPD), Spain.
                        </p>
                    </section>

                    {/* 9. CHANGES */}
                    <section className="pb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            9. Changes to This Policy
                        </h2>
                        <p className="text-sm text-zinc-400 leading-relaxed">
                            We reserve the right to update this privacy policy at any time.
                            Material changes will be communicated through the service. We
                            encourage you to review this page periodically.
                        </p>
                        <p className="text-sm text-zinc-500 mt-4 font-[var(--font-mono-acid)]">
                            Last updated: May 4, 2026
                        </p>
                    </section>
                </div>
            </div>
        </main>
    );
}
