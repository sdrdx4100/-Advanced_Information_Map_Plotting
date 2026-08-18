import type { Metadata } from "next";
import { headers } from "next/headers";
import { Noto_Sans_JP, Space_Mono } from "next/font/google";
import "./globals.css";

const noto = Noto_Sans_JP({ variable: "--font-noto", subsets: ["latin"] });
const space = Space_Mono({ variable: "--font-space", subsets: ["latin"], weight: ["400", "700"] });

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("host") ?? "localhost:3000";
  const protocol = requestHeaders.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  const image = `${protocol}://${host}/og.png`;
  const title = "ROAD SLOPE｜東名・新東名 道路勾配マップ";
  const description = "東名高速道路・新東名高速道路の道路縦断勾配を比較できるインタラクティブマップ。";
  return {
    title,
    description,
    openGraph: { title, description, type: "website", images: [{ url: image, width: 1733, height: 909 }] },
    twitter: { card: "summary_large_image", title, description, images: [image] },
  };
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ja"><body className={`${noto.variable} ${space.variable}`}>{children}</body></html>;
}
