import { cn } from "@/lib/utils"

function Skeleton({
    className,
    ...props
}: React.HTMLAttributes<HTMLDivElement>) {
    return (
        <div
            className={cn("animate-pulse bg-bg-3 border border-border", className)}
            {...props}
        />
    )
}

export { Skeleton }
