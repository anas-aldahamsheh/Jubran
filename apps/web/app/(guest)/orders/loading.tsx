import { OrdersBodySkeleton } from "@/components/customer/GuestSkeletons";

/** Opening "my orders": its shape shows at once under the guest frame. */
export default function OrdersLoading() {
  return <OrdersBodySkeleton />;
}
