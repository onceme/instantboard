// Responsive breakpoints in px, aligned with the CSS media queries.
// Sized against the W3Schools browser display statistics (top desktop
// resolutions: 1920x1080 18.4%, 1366x768 18.2%, 1280x1024/1280x800 ~1%
// each, "other high" 47.2% incl. 1536x864/1440x900/2560x1440) plus the
// dominant phone widths (360/375/390/393/414) and tablet widths
// (768/820/834 portrait, 1024+ landscape). See variables.css for the
// full tier-by-tier coverage table.
export const BREAKPOINTS = {
  xs: 0, // <640: phones (360x800, 390x844, 393x873, 414x896, ...)
  sm: 640, // 640-767: large phones / phablets, still mobile drawer layout
  md: 768, // 768-1023: tablets portrait (iPad 768/820/834), 60px icon rail
  lg: 1024, // 1024-1439: tablet landscape + laptops (1024x768, 1280x800, 1366x768)
  xl: 1440, // 1440-1919: large laptops / desktops (1440x900, 1536x864), right panel on
  xxl: 1920, // >=1920: full HD+ (1920x1080, 2560x1440), content capped at 1600px
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
// Theme selection mode: users may pin light/dark or follow the OS preference
export const DEFAULT_THEME_MODE = "system";
export const DEFAULT_COLOR_SCHEME = "chinese";
