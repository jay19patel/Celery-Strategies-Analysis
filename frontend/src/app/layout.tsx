import type { Metadata, Viewport } from "next";
import { JetBrains_Mono } from "next/font/google";

import { Providers } from "@/app/providers";
import "./globals.css";

const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono-face" });

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f2f2f7" },
    { media: "(prefers-color-scheme: dark)", color: "#000000" },
  ],
};

export const metadata: Metadata = {
  title: { default: "TradeBuddy", template: "%s · TradeBuddy" },
  description: "Automated and manual options trading on Delta Exchange India",
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "TradeBuddy",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={mono.variable}>
      <body className="font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
