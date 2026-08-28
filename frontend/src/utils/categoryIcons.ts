import {
  Folder,
  Trophy,
  Gamepad2,
  HeartPulse,
  GraduationCap,
  Music,
  Clapperboard,
  ShoppingCart,
  BookOpen,
  Plane,
  Rocket,
  Globe,
  Newspaper,
  Cpu,
  Star,
  Flag,
} from "lucide-vue-next";

// Category.icon stores a lucide icon name as a plain string (backend default is
// "folder"). CategoryEditor submits one of the curated picker options below, so
// stored values stay consistent with the seed-data naming; resolveCategoryIcon
// additionally accepts a few resolve-only entries (tech category's "cpu" plus
// legacy aliases) for values written outside the picker. Unknown names fall back
// to Folder instead of importing the full lucide icon map, keeping the bundle
// tree-shaken.
export type CategoryIconComponent = typeof Folder;

export interface CategoryIconOption {
  name: string;
  component: CategoryIconComponent;
}

// Curated icon choices rendered by the category editor (15 common options;
// "folder" first as the default selection)
export const CATEGORY_ICON_OPTIONS: CategoryIconOption[] = [
  { name: "folder", component: Folder },
  { name: "newspaper", component: Newspaper },
  { name: "globe", component: Globe },
  { name: "star", component: Star },
  { name: "flag", component: Flag },
  { name: "heart-pulse", component: HeartPulse },
  { name: "trophy", component: Trophy },
  { name: "gamepad-2", component: Gamepad2 },
  { name: "music", component: Music },
  { name: "clapperboard", component: Clapperboard },
  { name: "book-open", component: BookOpen },
  { name: "graduation-cap", component: GraduationCap },
  { name: "shopping-cart", component: ShoppingCart },
  { name: "plane", component: Plane },
  { name: "rocket", component: Rocket },
];

const CATEGORY_ICONS: Record<string, CategoryIconComponent> = {
  ...Object.fromEntries(
    CATEGORY_ICON_OPTIONS.map(
      ({ name, component }) => [name, component] as const,
    ),
  ),
  // Resolve-only entries kept out of the picker: the predefined tech category's
  // icon plus aliases for names older clients may have stored
  cpu: Cpu,
  gamepad: Gamepad2,
  heart: HeartPulse,
  film: Clapperboard,
  book: BookOpen,
  news: Newspaper,
};

export function resolveCategoryIcon(iconName?: string): CategoryIconComponent {
  return CATEGORY_ICONS[(iconName || "").toLowerCase()] ?? Folder;
}
