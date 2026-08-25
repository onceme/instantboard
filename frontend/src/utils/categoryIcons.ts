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
// "folder"; the category editor currently sends no icon, the API accepts any name).
// Curating the expected names here instead of importing the full lucide icon map
// keeps the bundle tree-shaken; unknown names fall back to Folder.
export type CategoryIconComponent = typeof Folder;

const CATEGORY_ICONS: Record<string, CategoryIconComponent> = {
  folder: Folder,
  trophy: Trophy,
  gamepad: Gamepad2,
  "gamepad-2": Gamepad2,
  heart: HeartPulse,
  "heart-pulse": HeartPulse,
  "graduation-cap": GraduationCap,
  music: Music,
  film: Clapperboard,
  clapperboard: Clapperboard,
  "shopping-cart": ShoppingCart,
  book: BookOpen,
  "book-open": BookOpen,
  plane: Plane,
  rocket: Rocket,
  globe: Globe,
  news: Newspaper,
  newspaper: Newspaper,
  cpu: Cpu,
  star: Star,
  flag: Flag,
};

export function resolveCategoryIcon(iconName?: string): CategoryIconComponent {
  return CATEGORY_ICONS[(iconName || "").toLowerCase()] ?? Folder;
}
