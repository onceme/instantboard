// Responsive breakpoints in px, aligned with the CSS media queries.
// Based on the most common screen resolutions (w3schools data):
// 1920x1080, 1536x864, 1366x768, 1280x720 plus mobile widths 360-430px.
export const BREAKPOINTS = {
  xs: 0, // <640: phones (360-430px wide)
  sm: 640, // 640-767: large phones / small tablets, still mobile layout
  md: 768, // 768-1023: tablets portrait/landscape, collapsed icon sidebar
  lg: 1024, // 1024-1439: laptops incl. 1366x768, full sidebar, no right panel
  xl: 1440, // 1440-1919: large laptops / desktops incl. 1536x864, right panel on
  xxl: 1920, // >=1920: full HD and above (1920x1080, 2K)
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
