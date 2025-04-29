import Toolbar from "@/components/Toolbar";
import Sidebar from "@/components/Sidebar";

const Layout = ({ children }: { children: React.ReactNode }) => {
  return (
    <div className="h-screen flex flex-col bg-gray-100">
      {/* Top fixed-height toolbar */}
      <Toolbar />

      {/* Below: Sidebar + Main content */}
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  );
};

export default Layout;
