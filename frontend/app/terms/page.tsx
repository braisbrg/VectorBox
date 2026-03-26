import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
    title: "Terms of Service — VectorBox",
    description: "Terms and conditions for using the VectorBox movie recommendation service.",
};

export default function TermsPage() {
    return (
        <main className="min-h-screen bg-black text-zinc-300">
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
                    Terms of Service
                </h1>

                {/* Sections */}
                <div className="space-y-0">
                    {/* 1. LEGAL NOTICE & ACCEPTANCE */}
                    <section className="border-b border-zinc-800 pb-8 mb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            1. Legal Notice &amp; Acceptance
                        </h2>
                        <p className="text-sm text-zinc-400 leading-relaxed mb-4">
                            In accordance with the Spanish Law on Information Society
                            Services (LSSI-CE), the following identifies the operator
                            of this service:
                        </p>
                        <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg p-4 text-sm font-[var(--font-mono-acid)] text-zinc-300 space-y-1 mb-4">
                            <p><span className="text-zinc-500">Operator:</span> VectorBox Project</p>
                            <p><span className="text-zinc-500">Nature:</span> Non-commercial personal project</p>
                            <p><span className="text-zinc-500">Country:</span> Spain</p>
                            <p><span className="text-zinc-500">Contact:</span> vectorbox.app@proton.me</p>
                        </div>
                        <p className="text-sm text-zinc-400 leading-relaxed">
                            By accessing or using VectorBox, you agree to be bound
                            by these Terms of Service. If you do not agree, you must
                            not use the service. VectorBox is intended for users aged
                            14 and over.
                        </p>
                    </section>

                    {/* 2. THE SERVICE */}
                    <section className="border-b border-zinc-800 pb-8 mb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            2. The Service
                        </h2>
                        <p className="text-sm text-zinc-400 leading-relaxed">
                            VectorBox provides AI-powered, personalized movie recommendations
                            based on your viewing history and preferences. The service is
                            currently offered in beta and is provided on an &quot;as is&quot;
                            basis without warranties of any kind, express or implied.
                        </p>
                    </section>

                    {/* 3. ACCOUNTS */}
                    <section className="border-b border-zinc-800 pb-8 mb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            3. Accounts
                        </h2>
                        <ul className="text-sm text-zinc-400 leading-relaxed space-y-3 list-disc list-inside">
                            <li>
                                You are responsible for maintaining the security of your PIN
                                and account credentials.
                            </li>
                            <li>
                                One account per person. Duplicate or shared accounts may be
                                suspended.
                            </li>
                            <li>
                                We reserve the right to suspend or terminate accounts that
                                violate these terms without prior notice.
                            </li>
                        </ul>
                    </section>

                    {/* 4. ACCEPTABLE USE */}
                    <section className="border-b border-zinc-800 pb-8 mb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            4. Acceptable Use
                        </h2>
                        <p className="text-sm text-zinc-400 leading-relaxed mb-3">
                            You agree not to:
                        </p>
                        <ul className="text-sm text-zinc-400 leading-relaxed space-y-3 list-disc list-inside">
                            <li>
                                Engage in automated scraping, crawling, or mass data
                                extraction from VectorBox.
                            </li>
                            <li>
                                Attempt to access data belonging to other users or circumvent
                                authentication mechanisms.
                            </li>
                            <li>
                                Conduct brute-force attacks, denial-of-service attacks, or any
                                other activity that disrupts the service.
                            </li>
                            <li>
                                Use the service for any unlawful purpose or in violation of
                                applicable laws.
                            </li>
                        </ul>
                    </section>

                    {/* 5. LETTERBOXD DATA */}
                    <section className="border-b border-zinc-800 pb-8 mb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            5. Letterboxd Data
                        </h2>
                        <p className="text-sm text-zinc-400 leading-relaxed">
                            VectorBox allows you to import your own movie rating data from
                            Letterboxd via CSV export or RSS feed. VectorBox is not affiliated
                            with, endorsed by, or connected to Letterboxd Ltd. You are solely
                            responsible for ensuring that you have the right to import and use
                            your Letterboxd data within VectorBox.
                        </p>
                    </section>

                    {/* 6. INTELLECTUAL PROPERTY */}
                    <section className="border-b border-zinc-800 pb-8 mb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            6. Intellectual Property
                        </h2>
                        <p className="text-sm text-zinc-400 leading-relaxed">
                            Movie metadata (titles, posters, synopses, cast) is sourced from
                            The Movie Database (TMDB). VectorBox does not claim ownership of
                            third-party movie metadata. The VectorBox name, logo, source code,
                            and design are the property of their respective creators and are
                            protected by applicable intellectual property laws.
                        </p>
                    </section>

                    {/* 7. LIMITATION OF LIABILITY */}
                    <section className="border-b border-zinc-800 pb-8 mb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            7. Limitation of Liability
                        </h2>
                        <p className="text-sm text-zinc-400 leading-relaxed">
                            During the beta period, VectorBox does not guarantee continuous,
                            uninterrupted availability of the service. We are not liable for
                            the accuracy, relevance, or completeness of any movie
                            recommendations. To the maximum extent permitted by law, VectorBox
                            shall not be liable for any indirect, incidental, or consequential
                            damages arising from the use of the service. VectorBox also assumes
                            no liability for service interruptions caused by third-party
                            infrastructure providers, including but not limited to Groq, OpenAI,
                            The Movie Database (TMDB), Qdrant, or cloud hosting providers.
                        </p>
                    </section>

                    {/* 8. GOVERNING LAW */}
                    <section className="border-b border-zinc-800 pb-8 mb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            8. Governing Law
                        </h2>
                        <p className="text-sm text-zinc-400 leading-relaxed">
                            These terms are governed by and construed in accordance with the
                            laws of Spain. Any disputes arising from these terms shall be
                            subject to the exclusive jurisdiction of the courts of Spain.
                        </p>
                    </section>

                    {/* 9. CONTACT */}
                    <section className="pb-8">
                        <h2 className="font-[var(--font-mono-acid)] text-[#CCFF00] text-sm uppercase tracking-widest mb-4">
                            9. Contact
                        </h2>
                        <p className="text-sm text-zinc-400 leading-relaxed">
                            For questions about these terms, contact us at{" "}
                            <span className="text-zinc-300">vectorbox.app@proton.me</span>.
                        </p>
                        <p className="text-sm text-zinc-500 mt-4 font-[var(--font-mono-acid)]">
                            Last updated: March 26, 2026
                        </p>
                    </section>
                </div>
            </div>
        </main>
    );
}
