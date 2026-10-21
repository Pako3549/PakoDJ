import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PakoDJ",
  description: "Discord music bot control panel",
};

// Theme resolution per docs/style.md: localStorage -> prefers-color-scheme -> dark.
const themeInit = `
(function () {
  var stored = localStorage.getItem('theme');
  var dark = stored ? stored === 'dark' : !window.matchMedia('(prefers-color-scheme: light)').matches;
  document.documentElement.classList.toggle('dark', dark);
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
      </head>
      <body className="bg-surface text-gray-100 font-sans antialiased min-h-screen transition-theme">
        {children}
      </body>
    </html>
  );
}
