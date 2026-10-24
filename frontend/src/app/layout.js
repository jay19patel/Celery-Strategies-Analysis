import "./globals.css";

export const metadata = {
  title: "TradeBuddy Pro Terminal — AI Algorithmic Trading & Analytics",
  description: "Next.js & FastAPI real-time algorithmic trading terminal with WebSocket streaming, SQLite SQLModel persistence, and Redis PubSub.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
