import { PageFade } from "@/components/common/PageFade";

/** Moving between the menu and my orders: the page fades in under the frame that stays. */
export default function GuestTemplate({ children }: { children: React.ReactNode }) {
  return <PageFade area="guest">{children}</PageFade>;
}
