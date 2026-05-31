import "../styles/globals.css";
import { ThemeProvider } from "@/components/theme-provider";
import { NavBar } from "@/components/nav-bar";

export const metadata = {
  title: "Grabon Intel",
  description: "Autonomous lead intelligence",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body>
        <ThemeProvider>
          <div className="flex h-screen flex-col">
            <NavBar />
            <div className="flex-1 overflow-hidden">{children}</div>
          </div>
        </ThemeProvider>
      </body>
    </html>
  );
}
