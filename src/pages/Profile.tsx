import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const Profile = () => {
    const [user, setUser] = useState<{ email: string; role: string } | null>(null);
    const [company, setCompany] = useState<{ id: number; name: string } | null>(null);
    const [error, setError] = useState("");
    const navigate = useNavigate();

    useEffect(() => {
        const fetchProfile = async () => {

            try {
                const response = await fetch(`${API_BASE_URL}/user_profile`, {
                    method: "GET",
                    credentials: "include",
                });

                if (!response.ok) {
                    const errorText = await response.text();
                    throw new Error(`Error ${response.status}: ${errorText}`);
                }

                const data = await response.json();
                setUser(data);

            } catch (err: any) {
                setError(err.message);
            }
        };
        const fetchCompany = async () => {
            try {
                const response = await fetch("/api/companies/my", {
                    method: "GET",
                    credentials: "include",
                });
    
                if (!response.ok) {
                    throw new Error(`Error ${response.status}`);
                }
    
                const data = await response.json();
                setCompany(data);
            } catch (err: any) {
                console.error("Company fetch error:", err.message);
            }
        };
        fetchCompany();
        fetchProfile();
    }, []);

    const userRole = localStorage.getItem("user_role");

    return (
        <div className="flex flex-col items-center justify-center min-h-screen">
            <h2 className="text-2xl font-bold mb-4">Profile</h2>
            {error && <p className="text-red-500">{error}</p>}
            {user ? (
                <div className="bg-gray-100 p-6 rounded shadow-md">
                    <p><strong>User:</strong> {user.email}</p>
                    <p><strong>Role:</strong> {user.role}</p>
                    {company && (
                        <p><strong>Company:</strong> {company.name}</p>
                    )}

                    {/* ✅ Show Admin Panel Button Only for Admins */}
                    {userRole === "admin" && (
                        <button className="bg-blue-500 text-white p-2 rounded mt-4" onClick={() => navigate("/admin")}>
                            Admin Panel
                        </button>
                    )}

                    <button
                        className="bg-red-500 text-white p-2 rounded mt-4"
                        onClick={async () => {
                            await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
                            //setIsAuthenticated(false); // ✅ Update state
                            navigate("/login"); // ✅ Redirect user
                        }}
                    >
                        Logout
                    </button>
                </div>
            ) : (
                <p>Loading profile...</p>
            )}
        </div>
    );
};

export default Profile;
