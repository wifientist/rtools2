import React, { useState, useEffect } from "react";
import { useAuth } from "@/context/AuthContext";

interface TenantSelectProps {
  value?: number | null;
  onChange?: (tenantId: number | null, tenantName: string | null) => void;
  label?: string;
  includeNullOption?: boolean;
}

const TenantSelect: React.FC<TenantSelectProps> = ({
  value,
  onChange,
  label = "Select Tenant",
  includeNullOption = false,
}) => {
  const { tenants } = useAuth();
  const [selectedId, setSelectedId] = useState<number | null>(value ?? null);

  useEffect(() => {
    setSelectedId(value ?? null);
  }, [value]);

  const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const id = e.target.value === "" ? null : parseInt(e.target.value, 10);
    const name = tenants.find(t => t.id === id)?.name || null;
    setSelectedId(id);
    if (onChange) {
      //console.log("TenantSelect onChange:", { id, name });
      onChange(id, name);
    }
  };

  return (
    <div className="field">
      <label className="label">{label}</label>
      <div className="control">
        <div className="select is-fullwidth">
          <select value={selectedId ?? ""} onChange={handleChange}>
            {includeNullOption && <option value="">-- None --</option>}
            {tenants.map((tenant) => (
              <option key={tenant.id} value={tenant.id}>
                {tenant.name}
              </option>
            ))}
          </select>
        </div>
      </div>
    </div>
  );
};

export default TenantSelect;
