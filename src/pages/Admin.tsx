import { useEffect, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { Building2, Users, Settings, Shield } from "lucide-react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const Admin = () => {
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const checkAdmin = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/auth/status`, {
          method: "GET",
          credentials: "include",
        });

        if (!response.ok) {
          navigate("/login");
          return;
        }

        const data = await response.json();
        console.log(data);
        if (data.role !== "admin") {
          navigate("/");
          return;
        }

        setIsAdmin(true);
      } catch (error) {
        navigate("/login");
      }
    };

    checkAdmin();
  }, [navigate]);

  if (isAdmin === null) {
    return <div>Loading...</div>;
  }

  const adminFeatures = [
    {
      title: "Company Management",
      description: "Manage company domains and approve signups",
      icon: <Building2 size={48} />,
      link: "/companies",
      color: "bg-blue-500",
    },
    {
      title: "User Management",
      description: "View and manage user accounts",
      icon: <Users size={48} />,
      link: "/users",
      color: "bg-green-500",
      disabled: false,
    },
    {
      title: "System Settings",
      description: "Configure system-wide settings",
      icon: <Settings size={48} />,
      link: "#",
      color: "bg-purple-500",
      disabled: true,
    },
    {
      title: "Security & Roles",
      description: "Manage roles and permissions",
      icon: <Shield size={48} />,
      link: "#",
      color: "bg-red-500",
      disabled: true,
    },
  ];

  return (
    <div className="container mx-auto p-6 max-w-6xl">
      <div className="mb-8">
        <h1 className="text-3xl font-bold mb-2">Admin Dashboard</h1>
        <p className="text-gray-600">Manage system settings and configurations</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {adminFeatures.map((feature) => (
          <Link
            key={feature.title}
            to={feature.link}
            className={`block p-6 bg-white rounded-lg shadow-md hover:shadow-lg transition ${
              feature.disabled
                ? "opacity-50 cursor-not-allowed pointer-events-none"
                : "hover:scale-105"
            }`}
          >
            <div
              className={`${feature.color} w-16 h-16 rounded-lg flex items-center justify-center text-white mb-4`}
            >
              {feature.icon}
            </div>
            <h3 className="text-xl font-semibold mb-2">{feature.title}</h3>
            <p className="text-gray-600 text-sm">{feature.description}</p>
            {feature.disabled && (
              <span className="inline-block mt-3 text-xs text-gray-500 italic">
                Coming soon
              </span>
            )}
          </Link>
        ))}
      </div>
    </div>
  );
};

export default Admin;
