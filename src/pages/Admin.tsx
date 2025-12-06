import { Link } from "react-router-dom";
import { Building2, Users, Settings, Shield } from "lucide-react";
import { useAuth } from "../context/AuthContext";

const Admin = () => {
  const { userRole } = useAuth();

  const adminFeatures = [
    {
      title: "Company Management",
      description: userRole === "super"
        ? "Manage all companies, domains, and approvals"
        : "View and manage your company settings",
      icon: <Building2 size={48} />,
      link: "/companies",
      color: "bg-blue-500",
    },
    {
      title: "User Management",
      description: userRole === "super"
        ? "Manage all users across all companies"
        : "Manage users in your company",
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
        <div className="flex items-center gap-3 mb-2">
          <h1 className="text-3xl font-bold">Admin Dashboard</h1>
          {userRole === "super" && (
            <span className="px-3 py-1 bg-purple-100 text-purple-800 text-sm font-semibold rounded-full">
              Super Admin
            </span>
          )}
        </div>
        <p className="text-gray-600">
          {userRole === "super"
            ? "Full system access - manage all companies and users"
            : "Company-scoped access - manage your company and users"}
        </p>
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
