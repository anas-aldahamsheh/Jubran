"use client";

import { useEffect, useState } from "react";
import { getCustomerSessionContext } from "@/lib/api";

/** "T4": the table number of this browser's visit, as printed on the table (null without a visit). */
export function formatTableNumber(tableNumber: string): string {
  return tableNumber.toUpperCase().startsWith("T") ? tableNumber : `T${tableNumber}`;
}

/** The guest's table number (shared request with the rest of the page). */
export function useTableNumber(): string | null {
  const [tableNumber, setTableNumber] = useState<string | null>(null);
  useEffect(() => {
    let ignore = false;
    getCustomerSessionContext()
      .then((context) => { if (!ignore) setTableNumber(context?.table_number ?? null); })
      .catch(() => undefined);
    return () => { ignore = true; };
  }, []);
  return tableNumber;
}
