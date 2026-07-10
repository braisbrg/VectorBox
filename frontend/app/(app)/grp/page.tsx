"use client";

import { GroupVibePicker } from "@/components/group-vibe-picker";
import { useShell } from "@/components/shell/shell-context";

export default function GroupsPage() {
    const { session } = useShell();
    return <GroupVibePicker currentUsername={session.username} />;
}
