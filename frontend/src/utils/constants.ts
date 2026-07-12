export const BREAKPOINTS = {
  xs: 0,
  sm: 768,
  md: 1024,
  lg: 1366,
  xl: 1920,
};

export const SIDEBAR_WIDTH = 220;
export const SIDEBAR_COLLAPSED_WIDTH = 60;
export const RIGHT_PANEL_WIDTH = 300;
export const CONTENT_PADDING = 16;
export const CARD_GAP = 12;

export const SSO_PROVIDERS = [
  {
    name: "Google",
    slug: "google",
    brand_color: "#4285F4",
    icon: "google-icon",
  },
  {
    name: "Azure AD",
    slug: "azure_ad",
    brand_color: "#0078D4",
    icon: "azure-icon",
  },
  {
    name: "GitHub",
    slug: "github",
    brand_color: "#333333",
    icon: "github-icon",
  },
  { name: "Apple", slug: "apple", brand_color: "#000000", icon: "apple-icon" },
  {
    name: "Facebook",
    slug: "facebook",
    brand_color: "#1877F2",
    icon: "facebook-icon",
  },
];

export const FINANCE_SUB_NAV_ITEMS = [
  { key: "overview", label: "Overview" },
  { key: "watchlist", label: "Watchlist" },
  { key: "search", label: "Search" },
  { key: "indices", label: "Indices" },
  { key: "commodities", label: "Commodities" },
];

export const MAX_WATCHLIST_ITEMS = 512;

export const SSE_HEARTBEAT_INTERVAL = 30_000;

export const DEFAULT_THEME = "light";
export const DEFAULT_COLOR_SCHEME = "chinese";
