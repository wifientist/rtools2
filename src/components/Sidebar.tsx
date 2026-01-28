import { Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { Home, Users, CloudCog, Camera, BookCheck, Settings, GitCompareArrows, ChevronRight, ChevronLeft, ArrowRightFromLine, Wifi, RedoDot, Activity, Table2, Network, Info, Lightbulb, Wrench, Shield, ChevronDown, Key, ListTodo, RefreshCcw, ClipboardList } from "lucide-react";
import { useState } from "react";

interface NavItem {
  to: string;
  icon: React.ReactNode;
  label: string;
  requiresAuth: boolean;
  rolesAllowed?: string[];
  requiresBeta?: boolean;
  isExternal?: boolean;
}

interface NavCategory {
  label: string;
  icon: React.ReactNode;
  items: NavItem[];
  defaultOpen?: boolean;
}

const Sidebar = () => {
  const { isAuthenticated, userRole, roleHierarchy, betaEnabled } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  const [openCategories, setOpenCategories] = useState<{ [key: string]: boolean }>({
    informational: true,
    explainers: true,
    helpers: true,
    admin: false,
  });

  // Toggle category open/closed
  const toggleCategory = (categoryKey: string) => {
    setOpenCategories(prev => ({
      ...prev,
      [categoryKey]: !prev[categoryKey]
    }));
  };

  // Standalone items (not in categories)
  const standaloneItems: NavItem[] = [
    { to: "/", icon: <Home size={22} />, label: "Home", requiresAuth: false },
    { to: "/controllers", icon: <Users size={22} />, label: "Controllers", requiresAuth: true, rolesAllowed: ["user","admin"] },
  ];

  // Categorized navigation structure
  const navCategories: NavCategory[] = [
    {
      label: "Informational",
      icon: <Info size={18} />,
      items: [
        { to: "/snapshot", icon: <Camera size={20} />, label: "MSP Snapshot", requiresAuth: true, rolesAllowed: ["user","admin"] },
        { to: "/diff", icon: <GitCompareArrows size={20} />, label: "Diff Tenant", requiresAuth: true, rolesAllowed: ["user","admin"] },
        { to: "/diff-venue", icon: <GitCompareArrows size={20} />, label: "Diff Venue", requiresAuth: true, rolesAllowed: ["user","admin"] },
        { to: "/sz-audit", icon: <ClipboardList size={20} />, label: "SZ Audit", requiresAuth: true, rolesAllowed: ["user","admin"] },
        { to: "/firmware-matrix", icon: <Table2 size={20} />, label: "Firmware Matrix", requiresAuth: true, rolesAllowed: ["user","admin"], requiresBeta: true },
        { to: "/diagrams", icon: <Network size={20} />, label: "Network Diagrams", requiresAuth: true, requiresBeta: true, isExternal: true },
      ],
    },
    {
      label: "Explainers",
      icon: <Lightbulb size={18} />,
      items: [
        { to: "/speed-explainer", icon: <Activity size={20} />, label: "Speed Explainer", requiresAuth: true, rolesAllowed: ["user","admin","super"] },
      ],
    },
    {
      label: "Helpers",
      icon: <Wrench size={18} />,
      items: [
        { to: "/migrate", icon: <RedoDot size={20} />, label: "Migrate R1→R1", requiresAuth: true, rolesAllowed: ["user","admin","super"] },
        { to: "/migrate-sz-to-r1", icon: <ArrowRightFromLine size={20} />, label: "Migrate SZ→R1", requiresAuth: true, rolesAllowed: ["user","admin","super"] },
        { to: "/per-unit-ssid", icon: <Wifi size={20} />, label: "Per-Unit SSID", requiresAuth: true, rolesAllowed: ["user","admin"], requiresBeta: true },
        { to: "/ap-port-config", icon: <Network size={20} />, label: "AP Port Config", requiresAuth: true, rolesAllowed: ["user","admin"], requiresBeta: true },
        { to: "/cloudpath-dpsk", icon: <Key size={20} />, label: "Cloudpath DPSK", requiresAuth: true, rolesAllowed: ["user","admin"], requiresBeta: true },
        { to: "/dpsk-orchestrator", icon: <RefreshCcw size={20} />, label: "DPSK Orchestrator", requiresAuth: true, rolesAllowed: ["user","admin"], requiresBeta: true },
        { to: "/option43", icon: <Camera size={20} />, label: "Option 43 Calc", requiresAuth: false },
      ],
    },
    {
      label: "Admin",
      icon: <Shield size={18} />,
      items: [
        { to: "/admin", icon: <Settings size={20} />, label: "Admin", requiresAuth: true, rolesAllowed: ["admin","super"] },
        { to: "/jobs", icon: <ListTodo size={20} />, label: "Jobs", requiresAuth: true, rolesAllowed: ["admin","super"] },
        { to: "/status", icon: <CloudCog size={20} />, label: "API Status", requiresAuth: true, rolesAllowed: ["admin","super"] },
        { to: "/super", icon: <Settings size={20} />, label: "Super", requiresAuth: true, rolesAllowed: ["super"] },
        { to: "/testcalls", icon: <BookCheck size={20} />, label: "Test Calls", requiresAuth: true, rolesAllowed: ["admin","super"] },
      ],
    },
  ];

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

  // Filter function for individual items
  const isItemVisible = (item: NavItem) => {
    // Always show items that don't require auth
    if (!item.requiresAuth) return true;

    // Hide all protected items if not authenticated (includes null during loading)
    if (isAuthenticated !== true) return false;

    // Hide "Admin" menu for super admins (they have "Super" menu instead)
    if (item.label === "Admin" && userRole === "super") return false;

    // Check beta access
    if (item.requiresBeta && !betaEnabled) return false;

    // Check role access
    return canAccess(item.rolesAllowed, userRole, roleHierarchy);
  };

  // Filter standalone items
  const visibleStandaloneItems = standaloneItems.filter(isItemVisible);

  // Filter categories - only show if they have visible items
  const visibleCategories = navCategories
    .map(category => ({
      ...category,
      items: category.items.filter(isItemVisible),
    }))
    .filter(category => category.items.length > 0);
   

  // Render a nav item (used for both standalone and category items)
  const renderNavItem = (item: NavItem, isNested = false, inCollapsedSubmenu = false) => {
    const className = `group flex items-center hover:text-gray-200 p-2 rounded hover:bg-gray-800 relative ${
      isNested ? 'pl-8 space-x-2' : 'space-x-3'
    } ${inCollapsedSubmenu ? 'w-full' : ''}`;

    const content = (
      <>
        {item.icon}
        {/* Show label when expanded OR in collapsed submenu */}
        {(!collapsed || inCollapsedSubmenu) && <span className="text-sm">{item.label}</span>}

        {/* When collapsed and NOT in submenu, show floating label on hover */}
        {collapsed && !inCollapsedSubmenu && (
          <span className="absolute left-16 bg-gray-700 text-gray-100 whitespace-nowrap rounded shadow px-2 py-1 text-sm opacity-0 group-hover:opacity-100 transition pointer-events-none z-50">
            {item.label}
          </span>
        )}
      </>
    );

    return item.isExternal ? (
      <a key={item.to} href={item.to} className={className}>
        {content}
      </a>
    ) : (
      <Link key={item.to} to={item.to} className={className}>
        {content}
      </Link>
    );
  };

  // Render collapsed category with hover submenu
  const renderCollapsedCategory = (category: NavCategory) => {
    const categoryKey = category.label.toLowerCase();

    return (
      <div key={categoryKey} className="group relative">
        {/* Category Icon */}
        <div className="flex items-center justify-center p-2 rounded hover:bg-gray-800 transition cursor-pointer">
          {category.icon}
        </div>

        {/* Floating submenu on hover - positioned to overlap slightly with icon to prevent gap */}
        <div className="absolute left-full top-0 ml-[-4px] bg-gray-700 text-gray-100 rounded shadow-lg border border-gray-600 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all duration-150 pointer-events-none group-hover:pointer-events-auto z-50 min-w-[180px]">
          {/* Category header in submenu */}
          <div className="px-3 py-2 border-b border-gray-600 font-semibold text-xs uppercase text-gray-400">
            {category.label}
          </div>
          {/* Category items */}
          <div className="py-1">
            {category.items.map(item => renderNavItem(item, false, true))}
          </div>
        </div>
      </div>
    );
  };

  return (
    <aside
      className={`relative flex flex-col bg-gray-700 shadow-lg transition-all duration-300
        ${collapsed ? "w-16" : "w-48"}
        h-full bg-gray-700 text-gray-100`}
    >
      {/* Navigation - scrollable area */}
      <nav className="flex-1 flex flex-col p-2 space-y-1 text-gray-200 overflow-y-auto overflow-x-hidden scrollbar-thin scrollbar-thumb-gray-600 scrollbar-track-gray-800">
        {/* Standalone items */}
        {visibleStandaloneItems.map(item => renderNavItem(item))}

        {/* Category divider */}
        {visibleStandaloneItems.length > 0 && visibleCategories.length > 0 && (
          <div className="border-t border-gray-600 my-2"></div>
        )}

        {/* Categorized items */}
        {visibleCategories.map((category) => {
          const categoryKey = category.label.toLowerCase();
          const isOpen = openCategories[categoryKey];

          // If collapsed, render with hover submenu
          if (collapsed) {
            return renderCollapsedCategory(category);
          }

          // If expanded, render normal accordion
          return (
            <div key={categoryKey}>
              {/* Category Header */}
              <button
                onClick={() => toggleCategory(categoryKey)}
                className="group w-full flex items-center justify-between p-2 rounded hover:bg-gray-800 transition"
              >
                <div className="flex items-center space-x-3">
                  {category.icon}
                  <span className="text-sm font-semibold">{category.label}</span>
                </div>
                <ChevronDown
                  size={16}
                  className={`transition-transform ${isOpen ? 'rotate-0' : '-rotate-90'}`}
                />
              </button>

              {/* Category Items */}
              {isOpen && (
                <div className="mt-1 space-y-1">
                  {category.items.map(item => renderNavItem(item, true))}
                </div>
              )}
            </div>
          );
        })}
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
