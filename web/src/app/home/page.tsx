import type { Metadata } from "next";
import { Rubik } from "next/font/google";
import { HomeLanding } from "@/components/home/home-landing";

const rubik = Rubik({ subsets: ["latin", "cyrillic"], weight: ["300", "400", "500", "700"] });

export const metadata: Metadata = { title: "VIA — контент-завод для видео" };

export default function HomePage() {
  return <HomeLanding fontClass={rubik.className} />;
}
