import { Moon, Sun } from "lucide-react";

import type { ThemeName } from "../theme";

type Props = {
  theme: ThemeName;
  onToggle: () => void;
};

export function ThemeToggle({ theme, onToggle }: Props) {
  const isDark = theme === "dark";
  return (
    <button
      type="button"
      className="icon-button"
      onClick={onToggle}
      aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
      title={isDark ? "Light theme" : "Dark theme"}
    >
      {isDark ? <Sun size={18} aria-hidden="true" /> : <Moon size={18} aria-hidden="true" />}
    </button>
  );
}

