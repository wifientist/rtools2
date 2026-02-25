import { useState } from "react";
import { Camera, GitCompareArrows } from "lucide-react";
import Snapshot from "@/pages/Snapshot";
import DiffTenant from "@/pages/DiffTenant";
import DiffVenue from "@/pages/DiffVenue";

type Tool = "snapshot" | "diff-tenant" | "diff-venue";

const tools: { key: Tool; label: string; icon: React.ReactNode; description: string }[] = [
  { key: "snapshot", label: "MSP Snapshot", icon: <Camera size={28} />, description: "View MSP details, ECs, labels, and entitlements" },
  { key: "diff-tenant", label: "Diff Tenant", icon: <GitCompareArrows size={28} />, description: "Compare two end-customer tenants side by side" },
  { key: "diff-venue", label: "Diff Venue", icon: <GitCompareArrows size={28} />, description: "Compare venue WiFi settings between tenants" },
];

function R1Details() {
  const [activeTool, setActiveTool] = useState<Tool | null>(null);

  return (
    <div className="p-4 max-w-7xl mx-auto">
      <h1 className="text-3xl font-bold mb-1">R1 Details</h1>
      <p className="text-gray-500 mb-6">RuckusONE informational tools</p>

      {/* Tool selector buttons */}
      <div className="grid grid-cols-3 gap-4 mb-8">
        {tools.map((tool) => {
          const isActive = activeTool === tool.key;
          return (
            <button
              key={tool.key}
              onClick={() => setActiveTool(isActive ? null : tool.key)}
              className={`
                flex flex-col items-center gap-2 p-6 rounded-xl border-2 transition-all cursor-pointer
                ${isActive
                  ? "border-blue-500 bg-blue-50 text-blue-700 shadow-md"
                  : "border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:bg-gray-50"
                }
              `}
            >
              <div className={`${isActive ? "text-blue-600" : "text-gray-400"}`}>
                {tool.icon}
              </div>
              <span className="font-semibold text-lg">{tool.label}</span>
              <span className={`text-xs text-center ${isActive ? "text-blue-500" : "text-gray-400"}`}>
                {tool.description}
              </span>
            </button>
          );
        })}
      </div>

      {/* Active tool content */}
      {activeTool === "snapshot" && <Snapshot />}
      {activeTool === "diff-tenant" && <DiffTenant />}
      {activeTool === "diff-venue" && <DiffVenue />}

      {!activeTool && (
        <div className="text-center py-16 text-gray-400">
          <p className="text-lg">Select a tool above to get started</p>
        </div>
      )}
    </div>
  );
}

export default R1Details;
