import { useState, useEffect } from "react";
import { Building2, Check, X, Plus, Trash2 } from "lucide-react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface Company {
  id: number;
  name: string;
  domain: string;
  is_approved: boolean;
  created_at: string | null;
  user_count: number;
}

export default function CompanyManager() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [currentUserRole, setCurrentUserRole] = useState<string>("admin");
  const [showForm, setShowForm] = useState(false);
  const [formData, setFormData] = useState({
    name: "",
    domain: "",
    is_approved: true,
  });

  // Fetch companies
  const fetchCompanies = async () => {
    try {
      setLoading(true);
      const response = await fetch(`${API_BASE_URL}/admin/companies`, {
        method: "GET",
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch companies: ${response.status}`);
      }

      const data = await response.json();
      setCompanies(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch companies");
    } finally {
      setLoading(false);
    }
  };

  // Fetch current user's role
  const fetchCurrentUserRole = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/auth/status`, {
        credentials: "include",
      });
      if (response.ok) {
        const data = await response.json();
        setCurrentUserRole(data.role || "admin");
      }
    } catch (err) {
      console.error("Error fetching current user role:", err);
    }
  };

  useEffect(() => {
    fetchCompanies();
    fetchCurrentUserRole();
  }, []);

  // Create new company
  const handleCreateCompany = async () => {
    if (!formData.name.trim() || !formData.domain.trim()) {
      alert("Name and domain are required");
      return;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/admin/companies`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(formData),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "Failed to create company");
      }

      setShowForm(false);
      setFormData({ name: "", domain: "", is_approved: true });
      await fetchCompanies();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create company");
    }
  };

  // Approve company
  const handleApprove = async (companyId: number) => {
    try {
      const response = await fetch(
        `${API_BASE_URL}/admin/companies/${companyId}/approve`,
        {
          method: "POST",
          credentials: "include",
        }
      );

      if (!response.ok) {
        throw new Error("Failed to approve company");
      }

      await fetchCompanies();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to approve company");
    }
  };

  // Unapprove company
  const handleUnapprove = async (companyId: number, companyName: string) => {
    if (
      !confirm(
        `Unapprove ${companyName}? New signups from this domain will be blocked.`
      )
    ) {
      return;
    }

    try {
      const response = await fetch(
        `${API_BASE_URL}/admin/companies/${companyId}/unapprove`,
        {
          method: "POST",
          credentials: "include",
        }
      );

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "Failed to unapprove company");
      }

      await fetchCompanies();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to unapprove company");
    }
  };

  // Delete company
  const handleDelete = async (companyId: number, companyName: string) => {
    if (
      !confirm(
        `Delete ${companyName}? This can only be done if no users are associated with this company.`
      )
    ) {
      return;
    }

    try {
      const response = await fetch(
        `${API_BASE_URL}/admin/companies/${companyId}`,
        {
          method: "DELETE",
          credentials: "include",
        }
      );

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "Failed to delete company");
      }

      await fetchCompanies();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete company");
    }
  };

  if (loading) {
    return (
      <div className="p-6">
        <div className="text-center">Loading companies...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="bg-red-100 text-red-700 p-4 rounded">
          Error: {error}
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Building2 size={32} className="text-blue-600" />
          <h2 className="text-3xl font-bold">Company Management</h2>
        </div>
        {currentUserRole === "super" && (
          <button
            onClick={() => setShowForm(!showForm)}
            className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 transition"
          >
            {showForm ? (
              <>
                <X size={18} />
                Cancel
              </>
            ) : (
              <>
                <Plus size={18} />
                Add Company
              </>
            )}
          </button>
        )}
      </div>

      {showForm && (
        <div className="bg-gray-100 p-6 rounded-lg mb-6 shadow-md">
          <h3 className="text-lg font-semibold mb-4">Create New Company</h3>
          <div className="space-y-3">
            <div>
              <label className="block text-sm font-medium mb-1">
                Company Name
              </label>
              <input
                type="text"
                className="w-full border border-gray-300 p-2 rounded focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                placeholder="Acme Corporation"
                value={formData.name}
                onChange={(e) =>
                  setFormData({ ...formData, name: e.target.value })
                }
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">
                Email Domain
              </label>
              <input
                type="text"
                className="w-full border border-gray-300 p-2 rounded focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                placeholder="acme.com"
                value={formData.domain}
                onChange={(e) =>
                  setFormData({ ...formData, domain: e.target.value })
                }
              />
            </div>
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="is_approved"
                checked={formData.is_approved}
                onChange={(e) =>
                  setFormData({ ...formData, is_approved: e.target.checked })
                }
                className="w-4 h-4"
              />
              <label htmlFor="is_approved" className="text-sm font-medium">
                Approve for signups immediately
              </label>
            </div>
            <button
              onClick={handleCreateCompany}
              className="px-6 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition"
            >
              Create Company
            </button>
          </div>
        </div>
      )}

      <div className="bg-white rounded-lg shadow-md overflow-hidden">
        <table className="w-full">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Company
              </th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Domain
              </th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Status
              </th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Users
              </th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Created
              </th>
              <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {companies.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-6 py-4 text-center text-gray-500">
                  No companies found
                </td>
              </tr>
            ) : (
              companies.map((company) => (
                <tr key={company.id} className="hover:bg-gray-50">
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-2">
                      <Building2 size={18} className="text-gray-400" />
                      <span className="font-medium">{company.name}</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-600">
                    @{company.domain}
                  </td>
                  <td className="px-6 py-4">
                    {company.is_approved ? (
                      <span className="inline-flex items-center gap-1 px-2 py-1 bg-green-100 text-green-800 text-xs font-medium rounded">
                        <Check size={14} />
                        Approved
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-1 bg-yellow-100 text-yellow-800 text-xs font-medium rounded">
                        <X size={14} />
                        Pending
                      </span>
                    )}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-600">
                    {company.user_count}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-600">
                    {company.created_at
                      ? new Date(company.created_at).toLocaleDateString()
                      : "—"}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex justify-end gap-2">
                      {company.is_approved ? (
                        <button
                          onClick={() =>
                            handleUnapprove(company.id, company.name)
                          }
                          disabled={company.id === -1}
                          className="text-yellow-600 hover:text-yellow-800 disabled:text-gray-400 disabled:cursor-not-allowed"
                          title={
                            company.id === -1
                              ? "Cannot unapprove Unassigned company"
                              : "Unapprove company"
                          }
                        >
                          <X size={18} />
                        </button>
                      ) : (
                        <button
                          onClick={() => handleApprove(company.id)}
                          className="text-green-600 hover:text-green-800"
                          title="Approve company"
                        >
                          <Check size={18} />
                        </button>
                      )}
                      {currentUserRole === "super" && (
                        <button
                          onClick={() => handleDelete(company.id, company.name)}
                          disabled={
                            company.id === -1 || company.user_count > 0
                          }
                          className="text-red-600 hover:text-red-800 disabled:text-gray-400 disabled:cursor-not-allowed"
                          title={
                            company.id === -1
                              ? "Cannot delete Unassigned company"
                              : company.user_count > 0
                              ? "Cannot delete company with users"
                              : "Delete company"
                          }
                        >
                          <Trash2 size={18} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-6 p-4 bg-blue-50 rounded-lg">
        <h4 className="font-semibold text-blue-900 mb-2">How it works:</h4>
        <ul className="text-sm text-blue-800 space-y-1">
          <li>
            • <strong>Approved</strong> companies allow users to sign up from
            their email domain
          </li>
          <li>
            • <strong>Pending</strong> companies are created automatically when
            someone tries to sign up, but signups are blocked
          </li>
          <li>
            • Click <Check size={14} className="inline" /> to approve a company
            and allow signups
          </li>
          <li>
            • Click <X size={14} className="inline" /> to unapprove (blocks new
            signups, existing users remain)
          </li>
          <li>
            • Companies can only be deleted if they have no associated users
          </li>
        </ul>
      </div>
    </div>
  );
}
