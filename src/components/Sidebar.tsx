import { Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { Home, Info, Users, CloudCog, Camera, Settings, GitCompareArrows, ChevronRight, ChevronLeft } from "lucide-react";
import { useState } from "react";

const Sidebar = () => {
  const { isAuthenticated, userRole, roleHierarchy } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  
  const navItems = [
    { to: "/", icon: <Home size={22} />, label: "Dashboard", requiresAuth: false },
    { to: "/about", icon: <Info size={22} />, label: "About", requiresAuth: false },
    { to: "/tenants", icon: <Users size={22} />, label: "Tenants", requiresAuth: true, rolesAllowed: ["user","admin"] },
    { to: "/snapshot", icon: <Camera size={22} />, label: "Snapshot", requiresAuth: true, rolesAllowed: ["user","admin"] },
    { to: "/diff", icon: <GitCompareArrows size={22} />, label: "Diff", requiresAuth: true, rolesAllowed: ["user","admin"] },
    { to: "/migrate", icon: <GitCompareArrows size={22} />, label: "Migrate", requiresAuth: true, rolesAllowed: ["user","admin"] },
    { to: "/status", icon: <CloudCog size={22} />, label: "API Status", requiresAuth: true, rolesAllowed: ["user","admin"] },
  ];

  if (userRole === "admin") {
    navItems.push({ to: "/admin", icon: <Settings size={22} />, label: "Admin", requiresAuth: true });
  }

  function canAccess(
    itemRoles: string[] | undefined,
    userRole: string | null,
    roleHierarchy: { [key: string]: number }
  ) {
    if (!itemRoles) return true;
    if (!userRole) return false;
  
    // 🚨 Protect against roleHierarchy not yet loaded
    if (!roleHierarchy || Object.keys(roleHierarchy).length === 0) return false;
  
    const userLevel = roleHierarchy[userRole] || 0;
    return itemRoles.some(role => userLevel >= (roleHierarchy[role] || 0));
  }
  

  // const visibleNavItems = navItems.filter(
  //   item => !item.requiresAuth || isAuthenticated
  // ); 
  const visibleNavItems = navItems.filter(item => {
    if (!item.requiresAuth) return true;
    if (!isAuthenticated) return false;
    return canAccess(item.rolesAllowed, userRole, roleHierarchy);
  });
   

  return (
    <aside
          className={`relative flex flex-col justify-between bg-gray-700 shadow-lg transition-all duration-300
            ${collapsed ? "w-16" : "w-48"} 
            h-full bg-gray-700 text-gray-100`}
        >

      {/* Navigation */}
      <nav className="flex flex-col p-2 space-y-2 text-gray-200">
        {visibleNavItems.map(({ to, icon, label }) => (
          <Link
            key={to}
            to={to}
            className="group flex items-center space-x-3 hover:text-gray-200 p-2 rounded hover:bg-gray-800 relative"
          >
            {icon}
            {/* Only show label if not collapsed */}
            {!collapsed && <span className="text-sm">{label}</span>}

            {/* When collapsed, show floating label on hover */}
            {collapsed && (
              <span
                className="absolute left-16 bg-white whitespace-nowrap rounded shadow px-2 py-1 text-sm opacity-0 group-hover:opacity-100 transition
                pointer-events-none z-50"
              >
                {label}
              </span>
            )}
          </Link>
        ))}
      </nav>

        {/* Collapse Button */}
      <div className="p-2 border-t flex justify-center">
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="flex items-center justify-center space-x-2 text-gray-100 hover:text-gray-400 transition"
        >
          {collapsed ? (
            <ChevronRight size={20} />
          ) : (
            <>
              <ChevronLeft size={20} />
              <span className="text-sm">Collapse</span>
            </>
          )}
        </button>
      </div>

      {/* Quarter-circle notch in top-left */}
      <svg
  className="absolute top-0 left-full w-8 h-8 z-10 pointer-events-none transform scale-x-[-1]"
  viewBox="0 0 32 32"
  xmlns="http://www.w3.org/2000/svg"
>
  <rect width="32" height="32" fill="#374151" /> {/* bg-gray-800 = sidebar background */}
  <path d="M0 0C17.673 0 32 14.327 32 32H0V0Z" fill="#f3f4f6" /> {/* bg-gray-100 = background color */}
</svg>





    </aside>
  );
};

export default Sidebar;
