import type { Metadata, Viewport } from "next";
import { EB_Garamond, Figtree } from "next/font/google";
import "./globals.css";
import { AppShell } from "@/components/AppShell";
import { PwaRegister } from "@/components/PwaRegister";

const garamond = EB_Garamond({
  subsets: ["latin", "cyrillic"],
  variable: "--font-garamond",
  display: "swap",
});

const figtree = Figtree({
  subsets: ["latin", "latin-ext"],
  variable: "--font-figtree",
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL("http://127.0.0.1:3000"),
  title: "Chronicum",
  description: "Персональная база знаний — кабинет материалов",
  applicationName: "Chronicum",
  manifest: "/site.webmanifest",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "Chronicum",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#0a0c0b",
  colorScheme: "dark",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body className={`${garamond.variable} ${figtree.variable} antialiased`}>
        <PwaRegister />
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
