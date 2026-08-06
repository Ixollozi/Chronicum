import type { Metadata } from "next";
import { EB_Garamond, Figtree } from "next/font/google";
import "./globals.css";
import { AppShell } from "@/components/AppShell";

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
  title: "Chronicum",
  description: "Персональная база знаний — кабинет материалов",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body className={`${garamond.variable} ${figtree.variable} antialiased`}>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
