import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "@/context/AuthContext";
import { apiGet, apiPost } from "@/utils/api";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const Profile = () => {
    const { userRole, betaEnabled, setBetaEnabled, checkAuth } = useAuth();

    const [user, setUser] = useState<{ email: string; role: string; beta_enabled: boolean } | null>(null);
    const [company, setCompany] = useState<{ id: number; name: string } | null>(null);
    const [error, setError] = useState("");
    const [updating, setUpdating] = useState(false);
    const navigate = useNavigate();

    useEffect(() => {
        const fetchProfile = async () => {
            try {
                // Uses apiFetch with auto-refresh on 401
                const data = await apiGet<{ email: string; role: string; beta_enabled: boolean }>(
                    `${API_BASE_URL}/user_profile`
                );
                setUser(data);
            } catch (err: any) {
                setError(err.message);
            }
        };

        const fetchCompany = async () => {
            try {
                // Uses apiFetch with auto-refresh on 401
                const data = await apiGet<{ id: number; name: string }>(
                    `${API_BASE_URL}/companies/my`
                );
                setCompany(data);
            } catch (err: any) {
                console.error("Company fetch error:", err.message);
            }
        };

        fetchCompany();
        fetchProfile();
    }, []);

    const handleBetaToggle = async () => {
        setUpdating(true);
        try {
            const newBetaState = !betaEnabled;
            // Uses apiFetch with auto-refresh on 401
            const data = await apiPost<{ beta_enabled: boolean }>(
                `${API_BASE_URL}/toggle_beta`,
                { beta_enabled: newBetaState }
            );
            setBetaEnabled(data.beta_enabled);

            // Refresh auth context to update token
            await checkAuth();
        } catch (err: any) {
            setError(err.message);
        } finally {
            setUpdating(false);
        }
    };

    return (
        <div className="flex flex-col items-center justify-center min-h-screen">
            <h2 className="text-2xl font-bold mb-4">Profile</h2>
            {error && <p className="text-red-500">{error}</p>}
            {user ? (
                <div className="bg-gray-100 p-6 rounded shadow-md space-y-6">
                    <p><strong>User:</strong> {user.email}</p>
                    <p><strong>Role:</strong> {user.role}</p>
                    {company && (
                        <p><strong>Company:</strong> {company.name}</p>
                    )}

                    {/* Beta Features Toggle */}
                    <div className="border-t pt-4">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="font-semibold">Beta Features</p>
                                <p className="text-sm text-gray-600">
                                    Enable access to experimental features
                                </p>
                            </div>
                            <button
                                onClick={handleBetaToggle}
                                disabled={updating}
                                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                                    betaEnabled ? "bg-blue-600" : "bg-gray-300"
                                } ${updating ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
                            >
                                <span
                                    className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                                        betaEnabled ? "translate-x-6" : "translate-x-1"
                                    }`}
                                />
                            </button>
                        </div>
                    </div>

                    {/* ✅ Show Admin Panel Button Only for Admins */}
                    {userRole === "admin" && (
                        <button className="bg-blue-500 text-white p-2 rounded mt-4" onClick={() => navigate("/admin")}>
                            Admin Panel
                        </button>
                    )}
                </div>
            ) : (
                <p>Loading profile...</p>
            )}

        </div>
    );
};

export default Profile;
