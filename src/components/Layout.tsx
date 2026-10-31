import Toolbar from "@/components/Toolbar";
import Sidebar from "@/components/Sidebar";
import { useState } from "react";

const Layout = ({ children }: { children: React.ReactNode }) => {
  // Bumped on a controller switch: the new key remounts the current page so it
  // refetches against the new controller, without a full browser reload.
  const [controllerSwitches, setControllerSwitches] = useState(0);

  return (
    <div className="h-screen flex flex-col bg-gray-100">
      {/* Top fixed-height toolbar */}
      <Toolbar onControllerSwitched={() => setControllerSwitches((n) => n + 1)} />

      {/* Below: Sidebar + Main content */}
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main key={controllerSwitches} className="flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  );
};

export default Layout;
