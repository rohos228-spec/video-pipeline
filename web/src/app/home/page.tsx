import type { Metadata } from "next";
import { HomeLanding } from "@/components/home/home-landing";

export const metadata: Metadata = { title: "VIA — контент-завод для видео" };

export default function HomePage() {
  return <HomeLanding fontClass="font-sans" />;
}
