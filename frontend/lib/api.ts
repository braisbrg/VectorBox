/**
 * API Client for CineMatch Backend
 * Security: Input sanitization, error handling
 */
import axios, { AxiosError } from "axios";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Create axios instance with defaults
const api = axios.create({
    baseURL: API_URL,
    timeout: 60000, // 60 seconds
    headers: {
        "Content-Type": "application/json",
    },
});

// Response interceptor for error handling
api.interceptors.response.use(
    (response) => response,
    (error: AxiosError) => {
        if (error.response) {
            // Server responded with error
            console.error("API Error:", error.response.data);
        } else if (error.request) {
            // Request made but no response
            console.error("Network Error:", error.message);
        }
        return Promise.reject(error);
    }
);

// Types
// Types
export interface User {
    id: number;
    username: string;
    created_at?: string;
    has_data?: boolean;
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
}

export interface FeedSection {
    id: string;
    title: string;
    type: string;
    items: FeedItem[];
}
// API Functions
export const uploadExportZIP = async (file: File, userId: number = 1) => {
    const formData = new FormData();
    formData.append("file", file);

    const response = await api.post(`/api/upload/export?user_id=${userId}`, formData, {
        headers: {
            "Content-Type": "multipart/form-data",
        },
    });

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

export const getUsers = async (): Promise<User[]> => {
    const response = await api.get("/api/users");
    return response.data;
};

export const getWildcardRecommendation = async (userId: number): Promise<FeedSection> => {
    const response = await api.get(`/api/recommendations/random-row?user_id=${userId}&scope=global`);
    return response.data;
};

export const getRandomRecommendation = async (userId: number, scope: string = "global"): Promise<FeedSection> => {
    const response = await api.get(`/api/recommendations/random-row?user_id=${userId}&scope=${scope}`);
    return response.data;
};

export const getHiddenGemsRecommendation = async (userId: number): Promise<FeedSection> => {
    const response = await api.get(`/api/recommendations/hidden-gems?user_id=${userId}`);
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
}

export const createUser = async (user: UserCreate): Promise<User> => {
    const response = await api.post("/api/users/", user);
    return response.data;
};

export const getFeed = async (
    userId: number,
    scope: "global" | "watchlist" = "global",
    countryCode: string = "ES",
    streamingProviders: number[] = [],
    includeLowQuality: boolean = false
): Promise<FeedResponse> => {
    const params = new URLSearchParams();
    params.append("user_id", userId.toString());
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
    userId: number,
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
    params.append("user_id", userId.toString());
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
