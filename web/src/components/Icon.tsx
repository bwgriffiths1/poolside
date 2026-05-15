import {
  Calendar,
  List,
  FileText,
  BookOpen,
  Plus,
  Search,
  Pencil,
  Download,
  Settings as SettingsIcon,
  Library,
  Check,
  X,
  ChevronRight,
  ChevronDown,
  ArrowRight,
  ArrowLeft,
  Filter,
  Lock,
  ExternalLink,
  Play,
  Sparkles,
  RefreshCw,
  Tag as TagIcon,
  Users,
  Circle,
  Globe,
} from "lucide-react";

const REGISTRY = {
  calendar: Calendar,
  list: List,
  doc: FileText,
  book: BookOpen,
  plus: Plus,
  search: Search,
  edit: Pencil,
  download: Download,
  settings: SettingsIcon,
  library: Library,
  check: Check,
  x: X,
  "chev-r": ChevronRight,
  "chev-d": ChevronDown,
  "arrow-r": ArrowRight,
  "arrow-l": ArrowLeft,
  filter: Filter,
  lock: Lock,
  external: ExternalLink,
  play: Play,
  spark: Sparkles,
  refresh: RefreshCw,
  tag: TagIcon,
  users: Users,
  dot: Circle,
  globe: Globe,
} as const;

export type IconName = keyof typeof REGISTRY;

interface IconProps {
  name: IconName;
  size?: number;
  className?: string;
}

export function Icon({ name, size = 14, className }: IconProps) {
  const Cmp = REGISTRY[name];
  if (!Cmp) return null;
  return <Cmp size={size} strokeWidth={1.6} className={className} />;
}
