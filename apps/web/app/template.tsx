import { PageFade } from "@/components/common/PageFade";

/** Page transitions between the site's areas (sign-in, table welcome, guest pages, admin). */
export default function Template({ children }: { children: React.ReactNode }) {
  return <PageFade area="site">{children}</PageFade>;
}
