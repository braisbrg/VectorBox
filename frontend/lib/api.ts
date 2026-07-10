/**
 * API Client for CineMatch Backend
 * Security: Input sanitization, error handling
 */
import axios, { AxiosError } from "axios";
import type { Contributor } from "@/types/feed";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Versioned localStorage key for the cached UserSession. Bump the suffix
// whenever UserSession's shape changes so old client caches are ignored
// instead of crashing on schema drift.
export const USER_SESSION_KEY = "vectorbox_user:v1";

// Create axios instance with defaults
export const api = axios.create({
    baseURL: typeof window === "undefined" ? API_URL : undefined, // Proxy on client, Direct on server
    timeout: 60000, // 60 seconds
    headers: {
        "Content-Type": "application/json",
    },
    withCredentials: true,
});

// Auth: primary path is the Clerk session JWT (attached by AuthBridge's request
// interceptor). Legacy httponly `vectorbox_token` cookie remains valid during
// the Clerk migration - `withCredentials: true` keeps it flowing.

api.interceptors.response.use(
    (response) => response,
    (error: AxiosError) => {
        if (error.response) {
            console.error("API Error:", error.response.data);
            if (error.response.status === 401 && typeof window !== "undefined") {
                localStorage.removeItem(USER_SESSION_KEY);
            }
        } else if (error.request) {
            console.error("Network Error:", error.message);
        }
        return Promise.reject(error);
    }
);

// Types
// Types
export interface UserSession {
    id: number;
    username: string; // vectorbox_handle
    token?: string;
    letterboxd_username?: string; // letterboxd_handle
    has_data?: boolean;
}

export interface VectorboxUser {
    id: number;
    username: string;
    created_at?: string;
    has_data?: boolean;
    letterboxd_username?: string;
}

// v1.1: Task progress tracking
export interface TaskStatus {
    task_id: string;
    status: "pending" | "processing" | "completed" | "failed";
    progress: number;
    step?: string;
}

export interface MovieMetadata {
    tmdb_id: number;
    title: string;
    original_title?: string;
    year?: number;
    runtime?: number;
    genres: string[];
    overview?: string;
    poster_path?: string;
    backdrop_path?: string;
    vote_average?: number;
    // Phase 12: VectorBox Score & i18n
    vectorbox_score?: number;
    imdb_rating?: number;
    metacritic_rating?: number;

    title_es?: string;
    overview_es?: string;
    release_dates?: Record<string, string>;
}

export interface ClusterInfo {
    cluster_id: number;
    label: string;
    movie_count: number;
    avg_rating: number;
    dominant_genres: string[];
    sample_movies: MovieMetadata[];
}

export interface RecommendationResponse {
    movie: MovieMetadata;
    similarity_score: number;
    streaming_available: boolean;
    streaming_providers: string[];
    providers?: string[];
    contributors?: Contributor[];
}

export interface FeedItem {
    id: number;
    title: string;
    poster_url?: string;
    match_score: number;
    streaming_providers: string[];
    year?: number;
    runtime?: number;
    letterboxd_uri?: string;
    rating?: number;
    overview?: string;
    contributors?: Contributor[];
    // Phase 12 Fields
    vectorbox_score?: number;
    imdb_rating?: number;
    metacritic_rating?: number;

    title_es?: string;
    overview_es?: string;
    letterboxd_rating?: number;
    release_dates?: Record<string, string>;
    backdrop_url?: string | null;
}

export interface FeedSection {
    id: string;
    title: string;
    type: string;
    items: FeedItem[];
}
// API Functions
export const uploadExportZIP = async (file: File): Promise<{
    status: string;
    message: string;
    movies_processed: number;
    movies_enriched: number;
    errors: string[];
    task_id: string;
}> => {
    const formData = new FormData();
    formData.append("file", file);

    const response = await api.post(`/api/upload/export`, formData, {
        headers: {
            "Content-Type": "multipart/form-data",
        },
    });

    return response.data;
};

// v1.1: Task progress polling
export const getTaskStatus = async (taskId: string): Promise<TaskStatus> => {
    const response = await api.get(`/api/tasks/${taskId}`);
    return response.data;
};

export const getUserClusters = async (userId: number): Promise<ClusterInfo[]> => {
    const response = await api.get(`/api/recommendations/clusters/${userId}`);
    return response.data;
};

// ---- Group rec (/grp) ----
export interface GroupMember {
    username: string;
    source: "vectorbox" | "letterboxd";
    films: number | null;
}

export interface GroupRecommendation {
    movie: {
        tmdb_id: number;
        title: string;
        year?: number;
        runtime?: number;
        genres: string[];
        overview?: string;
        poster_path?: string | null;
        vote_average?: number;
        vectorbox_score?: number | null;
        title_es?: string;
    };
    similarity_score: number;
    streaming_providers: string[];
    /** DB members who already have this film watchlisted (agreement matrix). */
    watchlisted_by: string[];
    contributors: { username: string; score: number }[];
}

export interface GroupVibeResponse {
    members: GroupMember[];
    recommendations: GroupRecommendation[];
}

export interface GroupVibeOptions {
    sources?: Record<string, "letterboxd" | "vectorbox">;
    /** "tonight favours X" — ranking leans on this member. */
    focus?: string | null;
    /** Session filters: runtime cap (min) + provider names. */
    maxRuntime?: number | null;
    providers?: string[];
}

export const getGroupVibe = async (usernames: string[], opts: GroupVibeOptions = {}): Promise<GroupVibeResponse> => {
    const response = await api.post("/api/rss/group/vibe", {
        usernames,
        sources: opts.sources,
        focus: opts.focus || undefined,
        max_runtime: opts.maxRuntime || undefined,
        providers: opts.providers?.length ? opts.providers : undefined,
    });
    return response.data;
};

export const syncRSS = async (username: string): Promise<{
    status: string;
    stats: Record<string, unknown>;
    message: string;
    task_id?: string | null;
}> => {
    const response = await api.post(`/api/rss/sync/${username}`);
    return response.data;
};

/** Whether a background RSS sync is still running (drives the sidebar spinner). */
export const getRssSyncStatus = async (): Promise<{ syncing: boolean }> => {
    const response = await api.get("/api/rss/sync-status");
    return response.data;
};

export const getUsers = async (): Promise<VectorboxUser[]> => {
    const response = await api.get("/api/users");
    return response.data;
};

// ... (omitted sections)
export const getWildcardRecommendation = async (): Promise<FeedSection> => {
    const response = await api.get(`/api/recommendations/random-row?scope=global`);
    return response.data;
};

export const getRandomRecommendation = async (scope: string = "global"): Promise<FeedSection> => {
    const response = await api.get(`/api/recommendations/random-row?scope=${scope}`);
    return response.data;
};

export const getHiddenGemsRecommendation = async (): Promise<FeedSection> => {
    const response = await api.get(`/api/recommendations/hidden-gems`);
    return response.data;
};

export interface FeedResponse {
    feed: FeedSection[];
    status?: "ok" | "incomplete" | "error";
}

export interface AuthResponse {
    token: string;
    user_id: number;
    username: string;
    has_data?: boolean;
    letterboxd_username?: string;
}

export const getCurrentUser = async (): Promise<AuthResponse> => {
    const response = await api.get("/api/auth/me");
    return response.data;
};

export const linkLetterboxd = async (
    userId: number,
    letterboxdUsername: string
): Promise<{ message: string; letterboxd_username: string }> => {
    // L-3: Username sent in body, not query param (privacy + CORS fix)
    const response = await api.patch(
        `/api/users/${userId}/link-letterboxd`,
        { letterboxd_username: letterboxdUsername }
    );
    return response.data;
};

export const getFeed = async (
    scope: "global" | "watchlist" = "global",
    countryCode: string = "ES",
    streamingProviders: number[] = []
): Promise<FeedResponse> => {
    const params = new URLSearchParams();
    params.append("scope", scope);
    params.append("country_code", countryCode);
    if (streamingProviders.length > 0) {
        params.append("streaming_providers", streamingProviders.join(","));
    }

    const response = await api.get(`/api/recommendations/feed?${params.toString()}`);
    return response.data;
};

export const getTMDBImageUrl = (path: string | null, size: string = "w500") => {
    if (!path) return "/placeholder-poster.png";
    return `https://image.tmdb.org/t/p/${size}${path}`;
};

// FIX-LB: Always build Letterboxd film URLs from tmdb_id. Stored letterboxd_uri values
// can point at user reviews ("/username/film/...") or shorturls ("boxd.it/..."), which
// don't resolve to the canonical film page.
export const getLetterboxdUrl = (tmdbId: number): string => {
    return `https://letterboxd.com/tmdb/${tmdbId}/`;
};

export const getWatchlist = async (
    page: number = 1,
    limit: number = 20,
    countryCode: string = "ES",
    filters: {
        sort_by?: string;
        runtime_min?: number;
        runtime_max?: number;
        year_min?: number;
        year_max?: number;
        genres?: string;
        min_rating?: number;
        streaming_providers?: string;
    } = {}
): Promise<{
    items: FeedItem[];
    total: number;
    page: number;
    limit: number;
    stats?: { total_runtime_min: number; total_films: number; upcoming: number };
}> => {
    const params = new URLSearchParams();
    params.append("page", page.toString());
    params.append("limit", limit.toString());
    params.append("country_code", countryCode);

    if (filters.sort_by) params.append("sort_by", filters.sort_by);
    if (filters.runtime_min) params.append("runtime_min", filters.runtime_min.toString());
    if (filters.runtime_max) params.append("runtime_max", filters.runtime_max.toString());
    if (filters.year_min) params.append("year_min", filters.year_min.toString());
    if (filters.year_max) params.append("year_max", filters.year_max.toString());
    if (filters.genres) params.append("genres", filters.genres);
    if (filters.min_rating) params.append("min_rating", filters.min_rating.toString());
    if (filters.streaming_providers) params.append("streaming_providers", filters.streaming_providers);

    const response = await api.get("/api/recommendations/watchlist", { params });
    return response.data;
};

/**
 * Reject a movie ("Not Interested")
 * Marks the movie so it won't appear in future recommendations.
 */
export const rejectMovie = async (tmdbId: number): Promise<{ status: string; tmdb_id: number; rejected: boolean }> => {
    const response = await api.post(`/api/recommendations/reject/${tmdbId}`);
    return response.data;
};

/**
 * Get all movies the user has rejected ("Not Interested").
 */
export interface RejectedMovie {
    tmdb_id: number;
    title: string;
    year?: number;
    poster_path?: string;
}

export const getRejectedMovies = async (): Promise<RejectedMovie[]> => {
    const response = await api.get("/api/recommendations/movies/rejected");
    return response.data;
};

/**
 * Undo a "Not Interested" rejection.
 */
export const unrejectMovie = async (tmdbId: number): Promise<{ status: string; tmdb_id: number; rejected: boolean }> => {
    const response = await api.delete(`/api/recommendations/movies/${tmdbId}/reject`);
    return response.data;
};

/**
 * Mark a movie as watched from the web (no rewatch tracking - ZIP/RSS still authoritative).
 */
export const markWatched = async (tmdbId: number): Promise<{ status: string; tmdb_id: number; watched: boolean }> => {
    const response = await api.post(`/api/recommendations/movies/${tmdbId}/watched`);
    return response.data;
};

/**
 * Reroll the "Niche Picks" cluster - advances to the next cluster on the next feed load.
 */
export const rerollCluster = async (): Promise<void> => {
    await api.post("/api/recommendations/feed/reroll-cluster");
};

// ---- Why-this breakdown (ACID why surfaces: rail D1 / dossier D2 / /why D3) ----
export interface WhyAnchor {
    tmdb_id: number;
    title: string;
    year?: number;
    rating?: number;
    weight: number;
    reason: string;
    poster_url?: string | null;
}

export interface WhyNeighbor {
    tmdb_id: number;
    title: string;
    year?: number;
    dist: number;
    poster_url?: string | null;
}

export interface WhyBreakdown {
    tmdb_id: number;
    title: string;
    year?: number;
    runtime?: number;
    director?: string | null;
    q?: number | null;
    poster_url?: string | null;
    trident: { vibe: number; auteur: number; gems: number };
    anchors: WhyAnchor[];
    neighbors: WhyNeighbor[];
    cluster?: { id: number; name: string; movie_count: number; avg_rating: number } | null;
    auteur?: { name: string; films_rated: number; note: string } | null;
    gem?: { vote_count: number; note: string } | null;
    rank?: number | null;
    rank_pool?: number | null;
}

export const getWhy = async (tmdbId: number): Promise<WhyBreakdown> => {
    const response = await api.get(`/api/recommendations/why/${tmdbId}`);
    return response.data;
};

// ---- Movie dossier ----
export interface MovieDetail {
    tmdb_id: number;
    title: string;
    year?: number;
    runtime?: number;
    genres: string[];
    overview?: string;
    poster_url?: string | null;
    backdrop_path?: string | null;
    directors?: string[];
    cast?: string[];
    tagline?: string | null;
    match_score: number;
    vectorbox_score?: number | null;
    imdb_rating?: number;
    metacritic_rating?: number;
    title_es?: string;
    overview_es?: string;
    streaming_providers?: string[];
}

export const getMovieDetail = async (tmdbId: number): Promise<MovieDetail> => {
    const response = await api.get(`/api/movies/${tmdbId}`);
    return response.data;
};

export interface SimilarMovie {
    movie_id: number;
    title: string;
    poster_path?: string | null;
    year?: number;
    similarity_score: number;
    vectorbox_score?: number | null;
    overview?: string;
    title_es?: string;
}

export const getSimilarMovies = async (tmdbId: number, limit: number = 10): Promise<SimilarMovie[]> => {
    // NOTE: the router is mounted under /api/recommendations (main.py:316) — the old
    // `/api/similar/{id}` path 404'd silently, so the dossier row never rendered.
    const response = await api.get(`/api/recommendations/similar/${tmdbId}?limit=${limit}`);
    return response.data.recommendations || [];
};

/** Add/remove a film on the user's watchlist (rating preserved server-side). */
export const setWatchlist = async (tmdbId: number, on: boolean): Promise<void> => {
    await api.post(`/api/movies/${tmdbId}/rate`, { is_watchlist: on });
};

// ---- Vector space (/space taste map) ----
export interface SpacePoint {
    tmdb_id: number;
    title: string;
    year?: number;
    poster_url?: string | null;
    x: number; // normalized [0,1]
    y: number;
    cluster_id: number | null;
    seen: boolean;
    q?: number | null;
    /** Cosine distance to the user's taste centroid in full 768-d space. */
    dc: number;
}

export interface SpaceResponse {
    points: SpacePoint[];
    centroid: { x: number; y: number } | null;
    clusters: { id: number; name: string }[];
    total: number;
}

export const getSpace = async (): Promise<SpaceResponse> => {
    const response = await api.get("/api/recommendations/space");
    return response.data;
};

// ---- Profile hub (/you) ----
export interface ProfileAggregates {
    username: string;
    letterboxd_username?: string | null;
    member_since?: string | null;
    tier: string;
    signature?: string | null;
    stats: {
        films: number;
        clusters: number;
        avg_rating: number | null;
        streak_days: number;
        this_month: number;
    };
    trident: { vibe: number; auteur: number; gems: number };
    activity_sparkline: number[];
    top_clusters: { id: number; name: string; films: number }[];
    /** Optional taste-card badge sources (real data). */
    top_director?: string | null;
    top_actor?: string | null;
    top_genres?: string[];
    /** Taste-card defining films — loved (≥4★/liked), highest first. */
    defining_films?: { tmdb_id: number; title: string; year?: number; poster_url?: string | null }[];
    recently_rated: { tmdb_id: number; title: string; year?: number; poster_url?: string | null; rating?: number; q?: number | null }[];
}

export const getMyProfile = async (): Promise<ProfileAggregates> => {
    const response = await api.get("/api/users/me/profile");
    return response.data;
};

/** Un-rate a film (profile "recently rated" → ✕). */
export const unrateFilm = async (tmdbId: number): Promise<void> => {
    await api.delete(`/api/users/me/ratings/${tmdbId}`);
};

export interface FilterSearchParams {
    yearMin?: number | null;
    yearMax?: number | null;
    maxRuntime?: number | null;
    /** Minimum VectorBox quality score Q (0-100). */
    minScore?: number | null;
    genres?: string[];
    /** ISO country for provider availability. */
    countryCode?: string;
    /** TMDB provider IDs to require. */
    providers?: number[];
}

// F8 — rail EXECUTE_QUERY as a SECTIONED feed (POST /recommendations/feed/filtered).
// Returns the full FeedResponse: year/runtime/genre/VBS apply as output filters over
// the live ranking (order-preserving), providers filter at-source, and rows without
// enough matches are dropped server-side.
export const getFilteredFeed = async (params: FilterSearchParams): Promise<FeedResponse> => {
    const response = await api.post("/api/recommendations/feed/filtered", {
        year_min: params.yearMin || null,
        year_max: params.yearMax || null,
        max_runtime: params.maxRuntime || null,
        min_score: params.minScore || null,
        genres: params.genres && params.genres.length ? params.genres : null,
        providers: params.providers && params.providers.length ? params.providers : null,
        country_code: params.countryCode || "ES",
    });
    return response.data as FeedResponse;
};

