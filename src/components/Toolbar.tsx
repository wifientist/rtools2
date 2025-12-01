import { Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { Bell, UserCircle, ChevronDown } from "lucide-react";
import { useState, useRef, useEffect } from "react";

const Toolbar = () => {
  const {
    isAuthenticated,
    activeControllerId,
    activeControllerName,
    secondaryControllerId,
    secondaryControllerName,
    controllers,
    setActiveControllerId,
    setActiveControllerName,
    setSecondaryControllerId,
    setSecondaryControllerName,
    logout
  } = useAuth();

  const [activeDropdownOpen, setActiveDropdownOpen] = useState(false);
  const [secondaryDropdownOpen, setSecondaryDropdownOpen] = useState(false);

  const activeDropdownRef = useRef<HTMLDivElement>(null);
  const secondaryDropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdowns when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (activeDropdownRef.current && !activeDropdownRef.current.contains(event.target as Node)) {
        setActiveDropdownOpen(false);
      }
      if (secondaryDropdownRef.current && !secondaryDropdownRef.current.contains(event.target as Node)) {
        setSecondaryDropdownOpen(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleActiveControllerSelect = async (controllerId: number, controllerName: string) => {
    try {
      const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';
      const response = await fetch(`${API_BASE_URL}/controllers/set-active`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ controller_id: controllerId })
      });

      if (response.ok) {
        // Update local state
        setActiveControllerId(controllerId);
        setActiveControllerName(controllerName);
        setActiveDropdownOpen(false);

        // Reload the page to refresh all components with new controller context
        window.location.reload();
      } else {
        console.error('Failed to set active controller:', response.status);
      }
    } catch (error) {
      console.error('Error setting active controller:', error);
    }
  };

  const handleSecondaryControllerSelect = async (controllerId: number | null, controllerName: string | null) => {
    try {
      const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

      if (controllerId === null || controllerId === 0) {
        // Clear secondary controller
        const response = await fetch(`${API_BASE_URL}/controllers/clear-secondary`, {
          method: 'POST',
          credentials: 'include'
        });

        if (response.ok) {
          setSecondaryDropdownOpen(false);
          // Reload the page to refresh all components
          window.location.reload();
        } else {
          console.error('Failed to clear secondary controller:', response.status);
        }
        return;
      }

      const response = await fetch(`${API_BASE_URL}/controllers/set-secondary`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ controller_id: controllerId })
      });

      if (response.ok) {
        // Update local state
        setSecondaryControllerId(controllerId);
        setSecondaryControllerName(controllerName || "");
        setSecondaryDropdownOpen(false);

        // Reload the page to refresh all components with new controller context
        window.location.reload();
      } else {
        console.error('Failed to set secondary controller:', response.status);
      }
    } catch (error) {
      console.error('Error setting secondary controller:', error);
    }
  };

  return (
    <header className="h-16 bg-gray-700 text-gray-100 flex items-center justify-between px-6 shadow-sm">
      <div className="h-16 flex items-center justify-center font-bold text-lg">
        RUCKUS.Tools
      </div>
      {isAuthenticated && (
        <>
          {/* Active Controller Selector */}
          <div className="relative" ref={activeDropdownRef}>
            <button
              onClick={() => setActiveDropdownOpen(!activeDropdownOpen)}
              className="text-sm flex items-center gap-2 hover:bg-gray-600 px-3 py-2 rounded transition-colors"
            >
              Active Controller: <span className="font-semibold">{activeControllerName || "None"}</span>
              <ChevronDown className="w-4 h-4" />
            </button>

            {activeDropdownOpen && (
              <div className="absolute top-full mt-1 left-0 bg-white text-gray-900 rounded-lg shadow-lg border border-gray-200 min-w-64 max-h-80 overflow-y-auto z-50">
                <div className="py-1">
                  {controllers.length === 0 ? (
                    <div className="px-4 py-2 text-sm text-gray-500">No controllers available</div>
                  ) : (
                    controllers.map((controller) => (
                      <button
                        key={controller.id}
                        onClick={() => handleActiveControllerSelect(controller.id, controller.name)}
                        className={`w-full text-left px-4 py-2 text-sm hover:bg-blue-50 transition-colors ${
                          controller.id === activeControllerId ? 'bg-blue-100 font-semibold' : ''
                        }`}
                      >
                        <div className="font-medium">{controller.name}</div>
                        <div className="text-xs text-gray-500">
                          {controller.controller_type}
                          {controller.controller_subtype && ` - ${controller.controller_subtype}`}
                        </div>
                      </button>
                    ))
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Secondary Controller Selector */}
          <div className="relative" ref={secondaryDropdownRef}>
            <button
              onClick={() => setSecondaryDropdownOpen(!secondaryDropdownOpen)}
              className="text-sm flex items-center gap-2 hover:bg-gray-600 px-3 py-2 rounded transition-colors"
            >
              Secondary Controller: <span className="font-semibold">{secondaryControllerName || "None"}</span>
              <ChevronDown className="w-4 h-4" />
            </button>

            {secondaryDropdownOpen && (
              <div className="absolute top-full mt-1 left-0 bg-white text-gray-900 rounded-lg shadow-lg border border-gray-200 min-w-64 max-h-80 overflow-y-auto z-50">
                <div className="py-1">
                  {/* Option to clear secondary controller */}
                  {secondaryControllerId && (
                    <>
                      <button
                        onClick={() => handleSecondaryControllerSelect(null, null)}
                        className="w-full text-left px-4 py-2 text-sm hover:bg-red-50 text-red-600 transition-colors border-b border-gray-200"
                      >
                        Clear Secondary Controller
                      </button>
                    </>
                  )}
                  {controllers.length === 0 ? (
                    <div className="px-4 py-2 text-sm text-gray-500">No controllers available</div>
                  ) : (
                    controllers.map((controller) => (
                      <button
                        key={controller.id}
                        onClick={() => handleSecondaryControllerSelect(controller.id, controller.name)}
                        className={`w-full text-left px-4 py-2 text-sm hover:bg-blue-50 transition-colors ${
                          controller.id === secondaryControllerId ? 'bg-blue-100 font-semibold' : ''
                        }`}
                      >
                        <div className="font-medium">{controller.name}</div>
                        <div className="text-xs text-gray-500">
                          {controller.controller_type}
                          {controller.controller_subtype && ` - ${controller.controller_subtype}`}
                        </div>
                      </button>
                    ))
                  )}
                </div>
              </div>
            )}
          </div>
        </>
      )}
      <div className="flex items-center space-x-4">
        <Bell className="w-5 h-5 text-gray-100 cursor-pointer" />
        
        {isAuthenticated ? 
          <>
          <Link to="/profile" className="hover:underline"><UserCircle className="w-6 h-6 text-gray-100" /></Link>
          <button onClick={logout} className="text-sm bg-red-500 text-white px-3 py-1 rounded">
            Logout
          </button>
          </>
          : (
          <>
            <a href="/login" className="text-sm text-gray-100 hover:bg-gray-800 space-x-3 p-2 rounded">Login</a>
          </>
        )}
      </div>
    </header>
  );
};

export default Toolbar;
