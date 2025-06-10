import { useState } from "react";
import { useAuth } from "@/context/AuthContext"; // assuming you have tenants stored here

import TenantActive from "@/components/TenantActive"; 
import TenantSecondary from "@/components/TenantSecondary";
import TenantCreator from "@/components/TenantCreator";
import TenantManager from "@/components/TenantManager";

export default function TenantPage() {
  const { activeTenantId, activeTenantName } = useAuth(); // or however you're tracking it
  const [connectionStatus, setConnectionStatus] = useState<"idle" | "success" | "error">("idle");
  const [loading, setLoading] = useState(false);

  return (
    <>
      <TenantManager />
    </>
  );
}
