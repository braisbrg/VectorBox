/**
 * API Client for CineMatch Backend
 * Security: Input sanitization, error handling
 */
import axios, { AxiosError } from "axios";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Create axios instance with defaults
const api = axios.create({
    baseURL: typeof window === "undefined" ? API_URL : undefined, // Proxy on client, Direct on server
    timeout: 60000, // 60 seconds
    headers: {
        "Content-Type": "application/json",
    },
    withCredentials: true,
});

// Request interceptor: Attach Token from LocalStorage
api.interceptors.request.use(
    (config) => {
        if (typeof window !== "undefined") {
            const userStr = localStorage.getItem("vectorbox_user");
            if (userStr) {
                try {
                    const user = JSON.parse(userStr);
                    if (user.token) {
                        console.log("Interceptor: Attaching Bearer token");
                        config.headers.Authorization = `Bearer ${user.token}`;
                    }
                } catch (e) {
                    console.error("Invalid user in localStorage", e);
                }
            }
        }
        return config;
    },
    (error) => Promise.reject(error)
);

// Response interceptor for error handling
api.interceptors.response.use(
    (response) => response,
    (error: AxiosError) => {
        if (error.response) {
            // Server responded with error
            console.error("API Error:", error.response.data);

            // v1.1: Auto-logout on 401 Unauthorized (Invalid/Expire Token)
            if (error.response.status === 401) {
                if (typeof window !== "undefined") {
                    localStorage.removeItem("vectorbox_user");
                    if (!window.location.pathname.includes("/login")) {
                        window.location.href = "/login";
                    }
                }
            }
        } else if (error.request) {
            // Request made but no response
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
    rotten_tomatoes_rating?: number;
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
    contributors?: { seed_title: string; contribution: number }[];
}

export interface RecommendationRequest {
    user_id: number;
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
    include_low_quality?: boolean;
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
    contributors?: { seed_title: string; contribution: number }[];
    // Phase 12 Fields
    vectorbox_score?: number;
    imdb_rating?: number;
    metacritic_rating?: number;
    rotten_tomatoes_rating?: number;
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
    stats: {
        rss_new_movies: number;
        rss_new_ratings: number;
        rss_updated_ratings: number;
        rss_errors: number;
        watchlist_added: number;
    };
    message: string;
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

export interface UserCreate {
    username: string;
    email?: string;
    country_code?: string;
}

export interface FeedResponse {
    feed: FeedSection[];
    status?: "ok" | "incomplete" | "error";
}

export const createUser = async (user: UserCreate): Promise<VectorboxUser> => {
    const response = await api.post("/api/users/", user);
    return response.data;
};

// v1.1: Authentication API
export interface AuthResponse {
    token: string;
    user_id: number;
    username: string;
    has_data?: boolean;
    letterboxd_username?: string;
}

export const register = async (
    username: string,
    pin: string,
    countryCode: string = "ES"
): Promise<AuthResponse> => {
    const response = await api.post("/api/auth/register", {
        username,
        pin,
        country_code: countryCode,
    });
    return response.data;
};

export const login = async (
    username: string,
    pin: string
): Promise<AuthResponse> => {
    const response = await api.post("/api/auth/login", {
        username: username.trim(),
        pin: pin.toString(),
    });
    return response.data;
};

export const logout = async (): Promise<void> => {
    await api.post("/api/auth/logout");
};

export const getCurrentUser = async (): Promise<AuthResponse> => {
    const response = await api.get("/api/auth/me");
    return response.data;
};

export const linkLetterboxd = async (
    userId: number,
    letterboxdUsername: string
): Promise<{ message: string; letterboxd_username: string }> => {
    const response = await api.patch(
        `/api/users/${userId}/link-letterboxd?letterboxd_username=${encodeURIComponent(letterboxdUsername)}`
    );
    return response.data;
};

export const getFeed = async (
    scope: "global" | "watchlist" = "global",
    countryCode: string = "ES",
    streamingProviders: number[] = [],
    includeLowQuality: boolean = false
): Promise<FeedResponse> => {
    const params = new URLSearchParams();
    params.append("scope", scope);
    params.append("country_code", countryCode);
    if (streamingProviders.length > 0) {
        params.append("streaming_providers", streamingProviders.join(","));
    }
    params.append("include_low_quality", includeLowQuality.toString());

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
    includeLowQuality: boolean = false,
    cookieHeader?: string
): Promise<FeedResponse | null> => {
    try {
        const params = new URLSearchParams();
        params.append("scope", scope);
        params.append("country_code", countryCode);
        if (streamingProviders.length > 0) {
            params.append("streaming_providers", streamingProviders.join(","));
        }
        params.append("include_low_quality", includeLowQuality.toString());

        const response = await fetch(
            `${API_URL}/api/recommendations/feed?${params.toString()}`,
            {
                cache: "no-store", // Dynamic data, always fresh
                headers: {
                    "Content-Type": "application/json",
                    ...(cookieHeader ? { Cookie: cookieHeader } : {}),
                },
            }
        );

        if (!response.ok) {
            console.error("SSR Feed fetch failed:", response.status);
            return null;
        }

        return response.json();
    } catch (error) {
        console.error("SSR Feed fetch error:", error);
        return null;
    }
};
