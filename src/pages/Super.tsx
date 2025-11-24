import { useEffect, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { Database, Activity, Terminal, Zap } from "lucide-react";
import Admin from "./Admin";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const Super = () => {
  const [isSuper, setIsSuper] = useState<boolean | null>(null);
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
        if (data.role !== "super") {
          navigate("/");
          return;
        }

        setIsSuper(true);
      } catch (error) {
        navigate("/login");
      }
    };

    checkAdmin();
  }, [navigate]);

  if (isSuper === null) {
    return <div>Loading...</div>;
  }

  const superFeatures = [
    {
      title: "System Status",
      description: "View API health, database status, and system metrics",
      icon: <Activity size={48} />,
      link: "/status",
      color: "bg-cyan-500",
    },
    {
      title: "Database Admin",
      description: "Manage migrations, backups, and database operations",
      icon: <Database size={48} />,
      link: "#",
      color: "bg-indigo-500",
      disabled: true,
    },
    {
      title: "API Console",
      description: "Test API endpoints and view logs",
      icon: <Terminal size={48} />,
      link: "/testcalls",
      color: "bg-orange-500",
    },
    {
      title: "Performance",
      description: "Monitor performance metrics and optimize",
      icon: <Zap size={48} />,
      link: "#",
      color: "bg-yellow-500",
      disabled: true,
    },
  ];

  return (
    <div className="space-y-8">
      {/* Super-only features section */}
      <div className="container mx-auto p-6 max-w-6xl">
        <div className="mb-8">
          <div className="flex items-center gap-3 mb-2">
            <h1 className="text-3xl font-bold">Super Admin Dashboard</h1>
            <span className="px-3 py-1 bg-purple-600 text-white text-sm font-semibold rounded-full">
              Super Admin
            </span>
          </div>
          <p className="text-gray-600">
            Full system access - platform-wide administration and monitoring
          </p>
        </div>

        <h2 className="text-xl font-semibold mb-4">System Administration</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
          {superFeatures.map((feature) => (
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

      {/* Include the Admin dashboard */}
      <div className="border-t-4 border-gray-200 pt-8">
        <Admin />
      </div>
    </div>
  );
};

export default Super;
