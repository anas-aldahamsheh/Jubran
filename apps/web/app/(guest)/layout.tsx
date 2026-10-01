import { GuestShell } from "@/components/customer/GuestShell";

/** The guest pages share one frame: the top bar and the tab dock stay while pages change. */
export default function GuestLayout({ children }: { children: React.ReactNode }) {
  return <GuestShell>{children}</GuestShell>;
}
