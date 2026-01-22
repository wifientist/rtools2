import { useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { useEffect } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export default function ControllerManager() {
  const { controllers, activeControllerId, secondaryControllerId, checkAuth } = useAuth();

  useEffect(() => {
    checkAuth(); // initial load
  }, []);

  const [showForm, setShowForm] = useState(false);
  const [editingControllerId, setEditingControllerId] = useState<number | null>(null);
  const [controllerType, setControllerType] = useState<"RuckusONE" | "SmartZone">("RuckusONE");
  const [formData, setFormData] = useState({
    name: "",
    // RuckusONE fields
    r1_tenant_id: "",
    r1_client_id: "",
    r1_shared_secret: "",
    r1_region: "NA",
    controller_subtype: "EC",
    // SmartZone fields
    sz_host: "",
    sz_port: "8443",
    sz_username: "",
    sz_password: "",
    sz_use_https: true,
    sz_version: "",
  });

  async function handleActiveControllerSelect(controllerId: number) {
    try {
      await fetch(`${API_BASE_URL}/controllers/set-active`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ controller_id: controllerId }),
      });
      await checkAuth();
    } catch (error) {
      console.error("Failed to switch controller", error);
    }
  }

  async function handleSecondaryControllerSelect(controllerId: number) {
    try {
      await fetch(`${API_BASE_URL}/controllers/set-secondary`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ controller_id: controllerId }),
      });
      await checkAuth();
    } catch (error) {
      console.error("Failed to switch controller", error);
    }
  }

  async function handleDeleteController(controllerId: number) {
    if (!confirm("Are you sure you want to delete this controller?")) return;

    try {
      await fetch(`${API_BASE_URL}/controllers/${controllerId}`, {
        method: "DELETE",
        credentials: "include",
      });
      await checkAuth();
    } catch (error) {
      console.error("Failed to delete controller", error);
    }
  }

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    setFormData(prev => ({
      ...prev,
      [e.target.name]: e.target.value,
    }));
  }

  async function handleAddController() {
    try {
      const endpoint = controllerType === "RuckusONE"
        ? `${API_BASE_URL}/controllers/new/ruckusone`
        : `${API_BASE_URL}/controllers/new/smartzone`;

      const payload = controllerType === "RuckusONE" ? {
        name: formData.name,
        controller_subtype: formData.controller_subtype,
        r1_tenant_id: formData.r1_tenant_id,
        r1_client_id: formData.r1_client_id,
        r1_shared_secret: formData.r1_shared_secret,
        r1_region: formData.r1_region,
      } : {
        name: formData.name,
        sz_host: formData.sz_host,
        sz_port: parseInt(formData.sz_port),
        sz_username: formData.sz_username,
        sz_password: formData.sz_password,
        sz_use_https: formData.sz_use_https,
        sz_version: formData.sz_version,
      };

      await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(payload),
      });
      setShowForm(false);
      resetFormData();
      await checkAuth();
    } catch (error) {
      console.error("Failed to add controller", error);
    }
  }

  function resetFormData() {
    setFormData({
      name: "",
      r1_tenant_id: "",
      r1_client_id: "",
      r1_shared_secret: "",
      r1_region: "NA",
      controller_subtype: "EC",
      sz_host: "",
      sz_port: "8443",
      sz_username: "",
      sz_password: "",
      sz_use_https: true,
      sz_version: "",
    });
    setControllerType("RuckusONE");
  }

  function handleEditController(controller: typeof controllers[0]) {
    setEditingControllerId(controller.id);
    setControllerType(controller.controller_type as "RuckusONE" | "SmartZone");

    if (controller.controller_type === "RuckusONE") {
      setFormData({
        name: controller.name,
        r1_tenant_id: controller.r1_tenant_id || "",
        r1_client_id: "",
        r1_shared_secret: "",
        r1_region: controller.r1_region || "NA",
        controller_subtype: controller.controller_subtype || "EC",
        sz_host: "",
        sz_port: "8443",
        sz_username: "",
        sz_password: "",
        sz_use_https: true,
        sz_version: "",
      });
    } else {
      setFormData({
        name: controller.name,
        r1_tenant_id: "",
        r1_client_id: "",
        r1_shared_secret: "",
        r1_region: "NA",
        controller_subtype: "EC",
        sz_host: controller.sz_host || "",
        sz_port: controller.sz_port?.toString() || "8443",
        sz_username: "",
        sz_password: "",
        sz_use_https: true,
        sz_version: controller.sz_version || "",
      });
    }
  }

  async function handleUpdateController() {
    if (!editingControllerId) return;

    try {
      const payload: any = { name: formData.name };

      if (controllerType === "RuckusONE") {
        payload.controller_subtype = formData.controller_subtype;
        payload.r1_tenant_id = formData.r1_tenant_id;
        payload.r1_region = formData.r1_region;
        if (formData.r1_client_id) payload.r1_client_id = formData.r1_client_id;
        if (formData.r1_shared_secret) payload.r1_shared_secret = formData.r1_shared_secret;
      } else {
        payload.sz_host = formData.sz_host;
        payload.sz_port = parseInt(formData.sz_port);
        payload.sz_use_https = formData.sz_use_https;
        payload.sz_version = formData.sz_version;
        if (formData.sz_username) payload.sz_username = formData.sz_username;
        if (formData.sz_password) payload.sz_password = formData.sz_password;
      }

      await fetch(`${API_BASE_URL}/controllers/${editingControllerId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(payload),
      });
      setEditingControllerId(null);
      resetFormData();
      await checkAuth();
    } catch (error) {
      console.error("Failed to update controller", error);
    }
  }

  function handleCancelEdit() {
    setEditingControllerId(null);
    resetFormData();
  }

  function getControllerTypeBadge(controller: typeof controllers[0]) {
    if (controller.controller_type === "RuckusONE") {
      const subtype = controller.controller_subtype || "EC";
      return (
        <div className="flex gap-1">
          <span className="text-xs px-2 py-1 rounded bg-blue-100 text-blue-700">
            RuckusONE
          </span>
          <span className={`text-xs px-2 py-1 rounded ${
            subtype === 'MSP'
              ? 'bg-purple-100 text-purple-700'
              : 'bg-gray-100 text-gray-700'
          }`}>
            {subtype}
          </span>
        </div>
      );
    } else {
      return (
        <span className="text-xs px-2 py-1 rounded bg-orange-100 text-orange-700">
          SmartZone
        </span>
      );
    }
  }

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h2 className="text-2xl font-bold mb-4">Manage Controllers</h2>

      <button
        onClick={() => setShowForm(prev => !prev)}
        className="mb-6 px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700"
      >
        {showForm ? "Cancel" : "Add New Controller"}
      </button>

      {showForm && (
        <div className="bg-gray-100 p-4 rounded-lg mb-6 space-y-3 shadow-inner">
          <div>
            <label className="block text-sm font-medium mb-1">Controller Type</label>
            <select
              value={controllerType}
              onChange={(e) => setControllerType(e.target.value as "RuckusONE" | "SmartZone")}
              className="w-full p-2 border rounded-md focus:ring-2 focus:ring-blue-300"
            >
              <option value="RuckusONE">RuckusONE</option>
              <option value="SmartZone">SmartZone</option>
            </select>
          </div>

          <input
            className="w-full border p-2 rounded"
            placeholder="Controller Name"
            name="name"
            value={formData.name}
            onChange={handleInputChange}
          />

          {controllerType === "RuckusONE" ? (
            <>
              <input
                className="w-full border p-2 rounded"
                placeholder="R1 Tenant ID"
                name="r1_tenant_id"
                value={formData.r1_tenant_id}
                onChange={handleInputChange}
              />
              <input
                className="w-full border p-2 rounded"
                placeholder="Client ID"
                name="r1_client_id"
                value={formData.r1_client_id}
                onChange={handleInputChange}
              />
              <input
                className="w-full border p-2 rounded"
                placeholder="Shared Secret"
                name="r1_shared_secret"
                value={formData.r1_shared_secret}
                onChange={handleInputChange}
              />
              <div>
                <label className="block text-sm font-medium mb-1">Region</label>
                <select
                  name="r1_region"
                  value={formData.r1_region}
                  onChange={(e) => setFormData(prev => ({ ...prev, r1_region: e.target.value }))}
                  className="w-full p-2 border rounded-md focus:ring-2 focus:ring-blue-300"
                >
                  <option value="NA">NA (North America)</option>
                  <option value="EU">EU (Europe)</option>
                  <option value="APAC">APAC (Asia Pacific)</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Controller Subtype</label>
                <select
                  name="controller_subtype"
                  value={formData.controller_subtype}
                  onChange={(e) => setFormData(prev => ({ ...prev, controller_subtype: e.target.value }))}
                  className="w-full p-2 border rounded-md focus:ring-2 focus:ring-blue-300"
                >
                  <option value="EC">EC (Enterprise Controller)</option>
                  <option value="MSP">MSP (Managed Service Provider)</option>
                </select>
                <p className="text-xs text-gray-500 mt-1">Select MSP for MSP tenant types with access to MSP-specific features</p>
              </div>
            </>
          ) : (
            <>
              <input
                className="w-full border p-2 rounded"
                placeholder="SmartZone Host"
                name="sz_host"
                value={formData.sz_host}
                onChange={handleInputChange}
              />
              <input
                className="w-full border p-2 rounded"
                placeholder="Port"
                name="sz_port"
                type="number"
                value={formData.sz_port}
                onChange={handleInputChange}
              />
              <input
                className="w-full border p-2 rounded"
                placeholder="Username"
                name="sz_username"
                value={formData.sz_username}
                onChange={handleInputChange}
              />
              <input
                className="w-full border p-2 rounded"
                placeholder="Password"
                type="password"
                name="sz_password"
                value={formData.sz_password}
                onChange={handleInputChange}
              />
              <input
                className="w-full border p-2 rounded"
                placeholder="API Version (e.g., v11_1)"
                name="sz_version"
                value={formData.sz_version}
                onChange={handleInputChange}
              />
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="sz_use_https"
                  checked={formData.sz_use_https}
                  onChange={(e) => setFormData(prev => ({ ...prev, sz_use_https: e.target.checked }))}
                  className="rounded"
                />
                <label htmlFor="sz_use_https" className="text-sm">Use HTTPS</label>
              </div>
            </>
          )}

          <button
            onClick={handleAddController}
            className="mt-2 px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            Save Controller
          </button>
        </div>
      )}

      {controllers.length === 0 ? (
        <p className="text-gray-600">No controllers found.</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {controllers.map(controller => (
            <div
              key={controller.id}
              className={`p-4 border rounded-lg shadow-sm transition-all ${
                controller.id === activeControllerId
                  ? "border-blue-500 bg-blue-50"
                  : controller.id === secondaryControllerId
                  ? "border-green-500 bg-green-50"
                  : "border-gray-200 hover:bg-gray-100"
              }`}
            >
              {editingControllerId === controller.id ? (
                <div className="space-y-3">
                  <h3 className="text-lg font-semibold mb-3">Edit Controller</h3>
                  <input
                    className="w-full border p-2 rounded"
                    placeholder="Controller Name"
                    name="name"
                    value={formData.name}
                    onChange={handleInputChange}
                  />

                  {controllerType === "RuckusONE" ? (
                    <>
                      <input
                        className="w-full border p-2 rounded"
                        placeholder="R1 Tenant ID"
                        name="r1_tenant_id"
                        value={formData.r1_tenant_id}
                        onChange={handleInputChange}
                      />
                      <input
                        className="w-full border p-2 rounded"
                        placeholder="Client ID (leave empty to keep current)"
                        name="r1_client_id"
                        value={formData.r1_client_id}
                        onChange={handleInputChange}
                      />
                      <input
                        className="w-full border p-2 rounded"
                        placeholder="Shared Secret (leave empty to keep current)"
                        name="r1_shared_secret"
                        value={formData.r1_shared_secret}
                        onChange={handleInputChange}
                      />
                      <div>
                        <label className="block text-sm font-medium mb-1">Region</label>
                        <select
                          name="r1_region"
                          value={formData.r1_region}
                          onChange={(e) => setFormData(prev => ({ ...prev, r1_region: e.target.value }))}
                          className="w-full p-2 border rounded-md focus:ring-2 focus:ring-blue-300"
                        >
                          <option value="NA">NA (North America)</option>
                          <option value="EU">EU (Europe)</option>
                          <option value="APAC">APAC (Asia Pacific)</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-sm font-medium mb-1">Controller Subtype</label>
                        <select
                          name="controller_subtype"
                          value={formData.controller_subtype}
                          onChange={(e) => setFormData(prev => ({ ...prev, controller_subtype: e.target.value }))}
                          className="w-full p-2 border rounded-md focus:ring-2 focus:ring-blue-300"
                        >
                          <option value="EC">EC (Enterprise Controller)</option>
                          <option value="MSP">MSP (Managed Service Provider)</option>
                        </select>
                      </div>
                    </>
                  ) : (
                    <>
                      <input
                        className="w-full border p-2 rounded"
                        placeholder="SmartZone Host"
                        name="sz_host"
                        value={formData.sz_host}
                        onChange={handleInputChange}
                      />
                      <input
                        className="w-full border p-2 rounded"
                        placeholder="Port"
                        name="sz_port"
                        type="number"
                        value={formData.sz_port}
                        onChange={handleInputChange}
                      />
                      <input
                        className="w-full border p-2 rounded"
                        placeholder="Username (leave empty to keep current)"
                        name="sz_username"
                        value={formData.sz_username}
                        onChange={handleInputChange}
                      />
                      <input
                        className="w-full border p-2 rounded"
                        placeholder="Password (leave empty to keep current)"
                        type="password"
                        name="sz_password"
                        value={formData.sz_password}
                        onChange={handleInputChange}
                      />
                      <input
                        className="w-full border p-2 rounded"
                        placeholder="Version"
                        name="sz_version"
                        value={formData.sz_version}
                        onChange={handleInputChange}
                      />
                    </>
                  )}

                  <div className="flex gap-2 mt-4">
                    <button
                      onClick={handleUpdateController}
                      className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
                    >
                      Save
                    </button>
                    <button
                      onClick={handleCancelEdit}
                      className="px-4 py-2 bg-gray-400 text-white rounded hover:bg-gray-500"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="flex justify-between items-start mb-2">
                    <div>
                      <h3 className="text-lg font-semibold">{controller.name}</h3>
                      {controller.controller_type === "RuckusONE" && controller.r1_tenant_id && (
                        <p className="text-xs text-gray-600">Tenant ID: {controller.r1_tenant_id}</p>
                      )}
                      {controller.controller_type === "SmartZone" && controller.sz_host && (
                        <p className="text-xs text-gray-600">Host: {controller.sz_host}</p>
                      )}
                      <div className="mt-1">
                        {getControllerTypeBadge(controller)}
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-1">
                      {controller.id === activeControllerId && (
                        <span className="text-xs text-blue-600 font-semibold">Active</span>
                      )}
                      {controller.id === secondaryControllerId && (
                        <span className="text-xs text-green-600 font-semibold">Secondary</span>
                      )}
                    </div>
                  </div>
                  <div className="flex flex-wrap justify-end gap-2 mt-4">
                    <button
                      onClick={() => handleEditController(controller)}
                      className="text-sm text-gray-600 hover:underline"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => handleActiveControllerSelect(controller.id)}
                      className="text-sm text-blue-600 hover:underline"
                    >
                      Set Active
                    </button>
                    <button
                      onClick={() => handleSecondaryControllerSelect(controller.id)}
                      className="text-sm text-green-600 hover:underline"
                    >
                      Set Secondary
                    </button>
                    <button
                      onClick={() => handleDeleteController(controller.id)}
                      className="text-sm text-red-600 hover:underline"
                    >
                      Delete
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
