export interface Contributor {
    type: "anchor" | "cluster" | "vibe" | "auteur" | "crowd";
    label?: string;
    seed_title?: string;
    seed_year?: number;
    seed_rating?: number;
    cluster_name?: string;
    medoid_title?: string;
    similarity?: number;
    score?: number;
}
