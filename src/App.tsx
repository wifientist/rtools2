import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import { useState, useEffect } from "react";

import Navbar from "@/components/Navbar";
import ProtectedRoute from "@/components/ProtectedRoute";
import AdminRoute from "@/components/AdminRoute";
import BetaRoute from "@/components/BetaRoute";

import { AuthProvider } from "@/context/AuthContext";

import Home from "@/pages/Home";
import Status from "@/pages/Status";
import Login from "@/pages/Login";
import Signup from "@/pages/Signup";
import Profile from "@/pages/Profile";
import Admin from "@/pages/Admin";
import Super from "@/pages/Super";
import Controllers from "@/pages/Controllers";
import DiffTenant from "@/pages/DiffTenant";
import DiffVenue from "@/pages/DiffVenue";
import MigrateR1ToR1 from "@/pages/MigrateR1ToR1";
import MigrateSzToR1 from "@/pages/MigrateSzToR1";
import Layout from "@/components/Layout";
import Snapshot from "@/pages/Snapshot";
import TestCalls from "@/pages/TestCalls";
import Option43Calculator from "@/pages/Option43Calculator";
import CompanyManager from "@/components/CompanyManager";
import Users from "@/pages/Users";
import PerUnitSSID from "@/pages/PerUnitSSID";
import APPortConfig from "@/pages/APPortConfig";
import APRename from "@/pages/APRename";
import SpeedExplainer from "@/pages/SpeedExplainer";
import RoamingExplainer from "@/pages/RoamingExplainer";
import FirmwareMatrix from "@/pages/FirmwareMatrix";
import CloudpathImport from "@/pages/CloudpathImport";
import DPSKOrchestrator from "@/pages/DPSKOrchestrator";
import JobMonitor from "@/pages/JobMonitor";
import JobList from "@/pages/JobList";
import SZAudit from "@/pages/SZAudit";
import DangerZone from "@/pages/DangerZone";
import FilesharePage from "@/pages/Fileshare/FilesharePage";
import FolderView from "@/pages/Fileshare/FolderView";
import FileshareAdmin from "@/pages/Fileshare/FileshareAdmin";

const App = () => {
  
  return (
    <AuthProvider>
    <Router>
      <Layout>
        {/*<div className="container mx-auto p-6">*/}
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/status" element={<AdminRoute element={<Status />} />} />
          <Route path="/login" element={<Login />} />
          <Route path="/signup" element={<Signup />} />
          <Route path="/profile" element={<ProtectedRoute element={<Profile />} />} />
          <Route path="/admin" element={<AdminRoute element={<Admin />} />} />
          <Route path="/super" element={<AdminRoute element={<Super />} />} />
          <Route path="/companies" element={<AdminRoute element={<CompanyManager />} />} />
          <Route path="/users" element={<AdminRoute element={<Users />} />} />
          <Route path="/controllers" element={<ProtectedRoute element={<Controllers />} />} />
          <Route path="/diff" element={<ProtectedRoute element={<DiffTenant />} />} />
          <Route path="/diff-venue" element={<ProtectedRoute element={<DiffVenue />} />} />
          <Route path="/speed-explainer" element={<SpeedExplainer />} />
          <Route path="/roaming-explainer" element={<RoamingExplainer />} />
          <Route path="/firmware-matrix" element={<BetaRoute element={<FirmwareMatrix />} />} />
          <Route path="/per-unit-ssid" element={<BetaRoute element={<PerUnitSSID />} />} />
          <Route path="/ap-port-config" element={<BetaRoute element={<APPortConfig />} />} />
          <Route path="/ap-rename" element={<BetaRoute element={<APRename />} />} />
          <Route path="/cloudpath-import" element={<BetaRoute element={<CloudpathImport />} />} />
          <Route path="/dpsk-orchestrator" element={<BetaRoute element={<DPSKOrchestrator />} />} />
          <Route path="/sz-audit" element={<ProtectedRoute element={<SZAudit />} />} />
          <Route path="/danger-zone" element={<BetaRoute element={<DangerZone />} />} />
          <Route path="/fileshare" element={<ProtectedRoute element={<FilesharePage />} />} />
          <Route path="/fileshare/admin" element={<AdminRoute element={<FileshareAdmin />} />} />
          <Route path="/fileshare/:folderSlug" element={<ProtectedRoute element={<FolderView />} />} />
          <Route path="/fileshare/:folderSlug/:subfolderSlug" element={<ProtectedRoute element={<FolderView />} />} />
          <Route path="/jobs" element={<ProtectedRoute element={<JobList />} />} />
          <Route path="/jobs/:jobId" element={<ProtectedRoute element={<JobMonitor />} />} />
          <Route path="/migrate" element={<ProtectedRoute element={<MigrateR1ToR1 />} />} />
          <Route path="/migrate-sz-to-r1" element={<ProtectedRoute element={<MigrateSzToR1 />} />} />
          <Route path="/snapshot" element={<ProtectedRoute element={<Snapshot />} />} />
          <Route path="/testcalls" element={<AdminRoute element={<TestCalls />} />} />
          <Route path="/option43" element={<Option43Calculator />} />
          
          {/* Protected routes */}
          <Route path="*" element={<Navigate to="/" />} /> 
        </Routes>
      {/*</div>*/}
      </Layout>
    </Router>
    </AuthProvider>
  );
};

export default App;
