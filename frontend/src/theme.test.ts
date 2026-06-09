import { getInitialTheme, storeTheme, THEME_STORAGE_KEY } from "./theme";

describe("theme storage", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("defaults to dark", () => {
    expect(getInitialTheme()).toBe("dark");
  });

  it("persists and restores a valid theme", () => {
    storeTheme("light");

    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(getInitialTheme()).toBe("light");
  });

  it("ignores invalid stored values", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "blue");

    expect(getInitialTheme()).toBe("dark");
  });
});

