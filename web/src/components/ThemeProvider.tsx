import type { ReactNode } from "react";
import { ThemeProvider as NextThemesProvider } from "next-themes";

export const THEME_STORAGE_KEY = "jerome-theme";

export default function ThemeProvider({ children }: { children: ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      storageKey={THEME_STORAGE_KEY}
      disableTransitionOnChange
    >
      {children}
    </NextThemesProvider>
  );
}
