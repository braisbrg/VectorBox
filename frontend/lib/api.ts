/**
 * API Client for CineMatch Backend
 * Security: Input sanitization, error handling
 */
import axios, { AxiosError } from "axios";
import type { Contributor } from "@/types/feed";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
// the Clerk migration — `withCredentials: true` keeps it flowing.

api.interceptors.response.use(
    (response) => response,
    (error: AxiosError) => {
        if (error.response) {
            console.error("API Error:", error.response.data);
            if (error.response.status === 401 && typeof window !== "undefined") {
                localStorage.removeItem("vectorbox_user");
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

export interface RecommendationRequest {
    // L-1: user_id removed — derived from JWT server-side
    cluster_id?: number;
    year_min?: number;
    year_max?: number;
    genres?: string[];
    runtime_min?: number;
    runtime_max?: number;
    streaming_providers?: number[];
    country_code?: string;
    limit?: number;
    min_vote_count?: number;
    min_rating?: number;
    original_language?: string;
    include_keywords?: string[];
    page?: number;
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

export const getRecommendationsByMood = async (
    request: RecommendationRequest
): Promise<RecommendationResponse[]> => {
    const response = await api.post("/api/recommendations/by-mood", request);
    return response.data;
};

export const getGeneralRecommendations = async (
    request: RecommendationRequest
): Promise<RecommendationResponse[]> => {
    const response = await api.post("/api/recommendations/general", request);
    return response.data;
};

export const getRandomMovieRecommendation = async (
    request: RecommendationRequest
): Promise<RecommendationResponse> => {
    const response = await api.post("/api/recommendations/random", request);
    return response.data;
};

export const getGroupVibe = async (usernames: string[]): Promise<RecommendationResponse[]> => {
    const response = await api.post("/api/rss/group/vibe", { usernames });
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

export const getUserActivity = async (username: string): Promise<{
    last_watched: { title: string; year: number; poster_path: string } | null;
    last_rated: { title: string; year: number; poster_path: string } | null;
}> => {
    const response = await api.get(`/api/users/${username}/activity`);
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

export interface GroupRecommendationRequest {
    user_ids: number[];
    year_min?: number;
    year_max?: number;
    genres?: string[];
    runtime_min?: number;
    runtime_max?: number;
    limit?: number;
}

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

export const logout = async (): Promise<void> => {
    // Clerk's signOut() handles real session invalidation; clear local cache.
    if (typeof window !== "undefined") {
        localStorage.removeItem("vectorbox_user");
    }
};

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

export const getGroupRecommendations = async (
    request: GroupRecommendationRequest
): Promise<RecommendationResponse[]> => {
    const response = await api.post("/api/recommendations/group", request);
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
): Promise<{ items: FeedItem[]; total: number; page: number; limit: number }> => {
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
 * Server-Side Feed Fetcher (for Next.js SSR)
 * Uses native fetch to work in Server Components
 * Forwards cookies for authentication
 */
export const getFeedServerSide = async (
    scope: "global" | "watchlist" = "global",
    countryCode: string = "ES",
    streamingProviders: number[] = [],
    cookieHeader?: string
): Promise<FeedResponse | null> => {
    try {
        const params = new URLSearchParams();
        params.append("scope", scope);
        params.append("country_code", countryCode);
        if (streamingProviders.length > 0) {
            params.append("streaming_providers", streamingProviders.join(","));
        }

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 10000); // 10s timeout

        try {
            const response = await fetch(
                `${API_URL}/api/recommendations/feed?${params.toString()}`,
                {
                    cache: "no-store", // Dynamic data, always fresh
                    signal: controller.signal,
                    headers: {
                        "Content-Type": "application/json",
                        ...(cookieHeader ? { Cookie: cookieHeader } : {}),
                    },
                }
            );

            if (!response.ok) {
                return null;
            }

            return response.json();
        } finally {
            clearTimeout(timeoutId);
        }
    } catch (error) {
        return null;
    }
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
 * Mark a movie as watched from the web (no rewatch tracking — ZIP/RSS still authoritative).
 */
export const markWatched = async (tmdbId: number): Promise<{ status: string; tmdb_id: number; watched: boolean }> => {
    const response = await api.post(`/api/recommendations/movies/${tmdbId}/watched`);
    return response.data;
};

/**
 * Reroll the "Niche Picks" cluster — advances to the next cluster on the next feed load.
 */
export const rerollCluster = async (): Promise<void> => {
    await api.post("/api/recommendations/feed/reroll-cluster");
};

export interface FilterSearchParams {
    yearMin?: number | null;
    yearMax?: number | null;
    maxRuntime?: number | null;
    minScore?: number | null;
    genres?: string[];
}

export const searchWithFilters = async (params: FilterSearchParams): Promise<FeedItem[]> => {
    const response = await api.post("/api/search/natural", {
        query: "quality films matching filters",
        forced_intent: {
            semantic_query: "quality films",
            year_min: params.yearMin || null,
            year_max: params.yearMax || null,
            max_runtime_minutes: params.maxRuntime || null,
            min_rating: params.minScore || null,
            popularity_vibe: "any",
            quality_gate_bypass: false,
            reasoning: "Filter search from UI",
        },
        country_code: "ES",
    });
    return (response.data.results || []).map((r: any): FeedItem => ({
        id: r.movie_id ?? r.id,
        title: r.title,
        poster_url: r.poster_path ?? r.poster_url,
        match_score: r.score ?? r.match_score ?? 0,
        streaming_providers: r.streaming_providers || [],
        year: r.year,
        runtime: r.runtime,
        overview: r.overview,
        vectorbox_score: r.vectorbox_score,
        rating: r.vote_average,
    }));
};

