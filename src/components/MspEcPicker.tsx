import { useEffect, useState } from "react";
import SingleEcSelector from "@/components/SingleEcSelector";

interface MspEcPickerProps {
  controllerId: number;
  ecId: string | null;
  ecName: string | null;
  onChange: (ecId: string | null, ecName: string | null) => void;
}

/**
 * Tenant step for tools on an MSP controller: pick one EC, then collapse to a
 * summary with a Change button so the venue picker below gets the space.
 */
export default function MspEcPicker({ controllerId, ecId, ecName, onChange }: MspEcPickerProps) {
  const [open, setOpen] = useState(!ecId);

  // A cleared EC (e.g. after a controller switch) reopens the table.
  useEffect(() => {
    if (!ecId) setOpen(true);
  }, [ecId]);

  if (ecId && !open) {
    return (
      <div className="flex items-center gap-3 text-sm bg-green-50 border border-green-200 rounded p-2">
        <span className="text-green-800">
          Tenant: <strong>{ecName || ecId}</strong>
        </span>
        <button
          onClick={() => setOpen(true)}
          className="ml-auto rounded border border-gray-300 bg-white px-3 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
        >
          Change
        </button>
      </div>
    );
  }

  return (
    <SingleEcSelector
      controllerId={controllerId}
      selectedEcId={ecId}
      onEcSelect={(id: string | null, ec: { name?: string }) => {
        onChange(id, ec?.name || null);
        if (id) setOpen(false);
      }}
    />
  );
}
