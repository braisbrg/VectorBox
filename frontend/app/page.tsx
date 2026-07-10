import { Landing } from "@/components/landing";

export const metadata = {
    title: "VectorBox",
};

// Root is the public landing (split-screen). Logged-in visitors are bounced to
// /feed by <Landing>. The former dashboard now lives under the (app) route group.
export default function HomePage() {
    return <Landing />;
}
