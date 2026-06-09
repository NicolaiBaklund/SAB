import { BarChart3, Database, LineChart, RadioTower } from "lucide-react";
import { useEffect, useState } from "react";

import { ReviewPage } from "./pages/ReviewPage";
import { getInitialTheme, storeTheme, type ThemeName } from "./theme";
import { ThemeToggle } from "./components/ThemeToggle";

const navItems = [
  { label: "Review", href: "/review", icon: Database, enabled: true },
  { label: "Sentiment", href: "/sentiment", icon: BarChart3, enabled: false },
  { label: "Signals", href: "/signals", icon: RadioTower, enabled: false },
  { label: "Projections", href: "/projections", icon: LineChart, enabled: false },
];

export default function App() {
  const [theme, setTheme] = useState<ThemeName>(() => getInitialTheme());

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    storeTheme(theme);
  }, [theme]);

  useEffect(() => {
    if (window.location.pathname === "/") {
      window.history.replaceState(null, "", "/review");
    }
  }, []);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">SAB</span>
          <span className="brand-subtitle">Sentiment Analysis Bot</span>
        </div>
        <nav className="nav-list" aria-label="Dashboard views">
          {navItems.map((item) => {
            const Icon = item.icon;
            const active = window.location.pathname === item.href;
            return item.enabled ? (
              <a
                className={`nav-item ${active ? "nav-item--active" : ""}`}
                href={item.href}
                key={item.href}
              >
                <Icon size={17} aria-hidden="true" />
                <span>{item.label}</span>
              </a>
            ) : (
              <span className="nav-item nav-item--disabled" key={item.href}>
                <Icon size={17} aria-hidden="true" />
                <span>{item.label}</span>
              </span>
            );
          })}
        </nav>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div>
            <h1>Article Review</h1>
            <p>Read-only review of ingested article sentiment.</p>
          </div>
          <ThemeToggle
            theme={theme}
            onToggle={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
          />
        </header>
        <ReviewPage />
      </main>
    </div>
  );
}
