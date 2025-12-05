// Common streaming provider IDs from TMDB/JustWatch
// Source: https://www.themoviedb.org/talk/5ec7caa79def490038ab156c

export const STREAMING_PROVIDERS = [
    { id: 8, name: "Netflix", logo_path: "/pbpMk2JmcoNnQwx5JGpXngfoWtp.jpg" },
    { id: 119, name: "Amazon Prime Video", logo_path: "/pvske1MyAoymrs5bguRfVqYiM9a.jpg" },
    { id: 337, name: "Disney Plus", logo_path: "/97yvRBw1GzX7fXprcF80er19ot.jpg" },
    { id: 350, name: "Apple TV Plus", logo_path: "/mcbz1LgtErU9p4UdbZ0rG6RTWHX.jpg" },
    { id: 1899, name: "Max", logo_path: "/jbe4gVSfRlbPTdESXhEKpornsfu.jpg" },
    { id: 531, name: "Paramount Plus", logo_path: "/fi83B1oztoS47xxcemFd8zbYD6z.jpg" },
    { id: 389, name: "Peacock", logo_path: "/gKNivSLPCUqFj0oZp5IdM1gl5oY.jpg" },
    { id: 283, name: "Crunchyroll", logo_path: "/fzN5Jok5Ig1eJ7gyNGoMhnLSCfh.jpg" },
    { id: 2, name: "Apple iTunes", logo_path: "/SPnB1qiCkYfirS2it3hZORwGVn.jpg" },
    { id: 3, name: "Google Play Movies", logo_path: "/8z7rC8uIDaTM91X0ZfkRf04ydj2.jpg" },
    { id: 149, name: "Movistar Plus", logo_path: "/f6TRLB3H4jDpFEZ0z2KWSSvu1SB.jpg" },
    { id: 63, name: "Filmin", logo_path: "/kO2SWXvDCHAquaUuTJBuZkTBAuU.jpg" },
] as const;

// Country-specific provider whitelists
export const COUNTRY_PROVIDERS: Record<string, number[]> = {
    ES: [8, 119, 337, 350, 1899, 149, 63], // Spain: Netflix, Prime, Disney+, Apple TV, Max, Movistar+, Filmin
    US: [8, 119, 337, 350, 1899, 531, 389], // US: Netflix, Prime, Disney+, Apple TV, Max, Paramount+, Peacock
    GB: [8, 119, 337, 350, 1899, 531], // UK: Netflix, Prime, Disney+, Apple TV, Max, Paramount+
    FR: [8, 119, 337, 350, 1899], // France: Netflix, Prime, Disney+, Apple TV, Max
    DE: [8, 119, 337, 350, 1899], // Germany: Netflix, Prime, Disney+, Apple TV, Max
    IT: [8, 119, 337, 350, 1899], // Italy: Netflix, Prime, Disney+, Apple TV, Max
    MX: [8, 119, 337, 350, 1899, 531], // Mexico: Netflix, Prime, Disney+, Apple TV, Max, Paramount+
    AR: [8, 119, 337, 350, 1899, 531], // Argentina: Netflix, Prime, Disney+, Apple TV, Max, Paramount+
    BR: [8, 119, 337, 350, 1899, 531], // Brazil: Netflix, Prime, Disney+, Apple TV, Max, Paramount+
    CA: [8, 119, 337, 350, 1899, 531], // Canada: Netflix, Prime, Disney+, Apple TV, Max, Paramount+
};

// Helper function to get providers for a specific country
export const getProvidersForCountry = (countryCode: string) => {
    const allowedIds = COUNTRY_PROVIDERS[countryCode] || COUNTRY_PROVIDERS["ES"];
    return STREAMING_PROVIDERS.filter(p => allowedIds.includes(p.id as number));
};

export const COUNTRIES = [
    { code: "ES", name: "España" },
    { code: "US", name: "United States" },
    { code: "GB", name: "United Kingdom" },
    { code: "FR", name: "France" },
    { code: "DE", name: "Germany" },
    { code: "IT", name: "Italy" },
    { code: "MX", name: "Mexico" },
    { code: "AR", name: "Argentina" },
    { code: "BR", name: "Brazil" },
    { code: "CA", name: "Canada" },
] as const;
