"use client";

import { MoreLikeThis } from "@/components/more-like-this";
import { useShell } from "@/components/shell/shell-context";

export default function MoreLikeThisPage() {
    const { session } = useShell();
    return <MoreLikeThis userId={session.id} />;
}
