import type { Metadata } from "next";
import { Bento } from "@/components/showcase/bento";
import { Blog } from "@/components/showcase/blog";
import { Changelog } from "@/components/showcase/changelog";
import { Chat } from "@/components/showcase/chat";
import { CodeBlock } from "@/components/showcase/code-block";
import { CommandPalette } from "@/components/showcase/command";
import { Comparison } from "@/components/showcase/comparison";
import { Contact } from "@/components/showcase/contact";
import { CookieBanner } from "@/components/showcase/cookie";
import { Cta } from "@/components/showcase/cta";
import { Faq } from "@/components/showcase/faq";
import { Footer } from "@/components/showcase/footer";
import { Hero } from "@/components/showcase/hero";
import { Integrations } from "@/components/showcase/integrations";
import { LogoCloud } from "@/components/showcase/logo-cloud";
import { Marquee } from "@/components/showcase/marquee";
import { Navbar } from "@/components/showcase/navbar";
import { Newsletter } from "@/components/showcase/newsletter";
import { Notifications } from "@/components/showcase/notifications";
import { PaginationDemo } from "@/components/showcase/pagination";
import { Pricing } from "@/components/showcase/pricing";
import { ProfileCard } from "@/components/showcase/profile";
import { Stats } from "@/components/showcase/stats";
import { Stepper } from "@/components/showcase/stepper";
import { TabsDemo } from "@/components/showcase/tabs";
import { Team } from "@/components/showcase/team";
import { Testimonials } from "@/components/showcase/testimonials";
import { Timeline } from "@/components/showcase/timeline";
import { UiGallery } from "@/components/showcase/ui-gallery";

export const metadata: Metadata = {
  title: "UI Showcase | Draft Dashboard",
  description: "A catalog of every UI section available in the app.",
};

function Marker({ label }: { label: string }) {
  return (
    <div className="mx-auto max-w-6xl px-6 pt-8">
      <div className="inline-flex items-center gap-2 rounded-full border border-dashed border-border bg-muted/40 px-3 py-1 font-mono text-xs text-muted-foreground">
        <span className="h-1.5 w-1.5 rounded-full bg-primary" />
        {label}
      </div>
    </div>
  );
}

export default function ShowcasePage() {
  return (
    <main className="flex-1">
      <Navbar />
      <Marker label="01 - hero" />
      <Hero />
      <Marker label="02 - logo cloud" />
      <LogoCloud />
      <Marker label="03 - bento features" />
      <Bento />
      <Marker label="04 - integrations grid" />
      <Integrations />
      <Marker label="05 - stats counter" />
      <Stats />
      <Marker label="06 - comparison table" />
      <Comparison />
      <Marker label="07 - pricing" />
      <Pricing />
      <Marker label="08 - tabs" />
      <TabsDemo />
      <Marker label="09 - stepper" />
      <Stepper />
      <Marker label="10 - testimonial slider" />
      <Testimonials />
      <Marker label="11 - marquee" />
      <Marquee />
      <Marker label="12 - team" />
      <Team />
      <Marker label="13 - profile card" />
      <ProfileCard />
      <Marker label="14 - timeline" />
      <Timeline />
      <Marker label="15 - changelog" />
      <Changelog />
      <Marker label="16 - blog" />
      <Blog />
      <Marker label="17 - ui gallery" />
      <UiGallery />
      <Marker label="18 - chat" />
      <Chat />
      <Marker label="19 - command palette" />
      <CommandPalette />
      <Marker label="20 - code block" />
      <CodeBlock />
      <Marker label="21 - notifications" />
      <Notifications />
      <Marker label="22 - newsletter" />
      <Newsletter />
      <Marker label="23 - contact" />
      <Contact />
      <Marker label="24 - faq accordion" />
      <Faq />
      <Marker label="25 - pagination + breadcrumbs" />
      <PaginationDemo />
      <Marker label="26 - cookie banner" />
      <CookieBanner />
      <Marker label="27 - shiny cta" />
      <Cta />
      <Marker label="28 - footer" />
      <Footer />
    </main>
  );
}
