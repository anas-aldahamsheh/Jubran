import { AdminGate } from "@/components/admin/AdminGate";
import { AdminHeader } from "@/components/admin/AdminHeader";

/**
 * Every admin page: the sign-in check first, then one header (and the phone's tab bar) that
 * stays in place while the pages change under it.
 */
export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <AdminGate>
      <div className="flex min-h-dvh flex-col bg-canvas text-ink">
        <AdminHeader />
        {children}
      </div>
    </AdminGate>
  );
}
