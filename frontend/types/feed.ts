export interface Contributor {
    type: "anchor" | "cluster" | "vibe" | "auteur" | "crowd" | "cult_actor" | "watchlist";
    label?: string;
    seed_title?: string;
    seed_year?: number;
    seed_rating?: number;
    cluster_name?: string;
    medoid_title?: string;
    similarity?: number;
    score?: number;
    director?: string;
    actor?: string;
}
