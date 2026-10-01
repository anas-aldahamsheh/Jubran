import { PageFade } from "@/components/common/PageFade";

/** Moving between admin pages: the page fades in under the header that stays. */
export default function AdminTemplate({ children }: { children: React.ReactNode }) {
  return <PageFade area="admin">{children}</PageFade>;
}
