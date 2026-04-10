import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import { useState, useEffect } from "react";

import Navbar from "@/components/Navbar";
import ProtectedRoute from "@/components/ProtectedRoute";
import AdminRoute from "@/components/AdminRoute";
import BetaRoute from "@/components/BetaRoute";
import AlphaRoute from "@/components/AlphaRoute";

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
import R1Details from "@/pages/R1Details";
import TestCalls from "@/pages/TestCalls";
import Option43Calculator from "@/pages/Option43Calculator";
import CompanyManager from "@/components/CompanyManager";
import Users from "@/pages/Users";
import PerUnitSSID from "@/pages/PerUnitSSID";
import APPortConfig from "@/pages/APPortConfig";
import APRename from "@/pages/APRename";
import BulkWlanEdit from "@/pages/BulkWlanEdit";
import MigrationDashboard from "@/pages/MigrationDashboard";
import SpeedExplainer from "@/pages/SpeedExplainer";
import RoamingExplainer from "@/pages/RoamingExplainer";
import FirmwareMatrix from "@/pages/FirmwareMatrix";
import CloudpathImport from "@/pages/CloudpathImport";
import DPSKOrchestrator from "@/pages/DPSKOrchestrator";
import MigrateSzToR1Config from "@/pages/MigrateSzToR1Config";
import MigrationAudit from "@/pages/MigrationAudit";
import JobMonitor from "@/pages/JobMonitor";
import JobList from "@/pages/JobList";
import SZAudit from "@/pages/SZAudit";
import APPopAndSwap from "@/pages/APPopAndSwap";
import BulkAPTagging from "@/pages/BulkAPTagging";
import DangerZone from "@/pages/DangerZone";
import DataStudioExport from "@/pages/DataStudioExport";
import DfsBlacklist from "@/pages/DfsBlacklist";
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
          <Route path="/r1-details" element={<ProtectedRoute element={<R1Details />} />} />
          <Route path="/diff" element={<Navigate to="/r1-details" />} />
          <Route path="/diff-venue" element={<Navigate to="/r1-details" />} />
          <Route path="/speed-explainer" element={<SpeedExplainer />} />
          <Route path="/roaming-explainer" element={<RoamingExplainer />} />
          <Route path="/firmware-matrix" element={<BetaRoute element={<FirmwareMatrix />} />} />
          <Route path="/per-unit-ssid" element={<ProtectedRoute element={<PerUnitSSID />} />} />
          <Route path="/ap-port-config" element={<ProtectedRoute element={<APPortConfig />} />} />
          <Route path="/ap-rename" element={<ProtectedRoute element={<APRename />} />} />
          <Route path="/bulk-wlan" element={<ProtectedRoute element={<BulkWlanEdit />} />} />
          <Route path="/pop-swap" element={<AlphaRoute element={<APPopAndSwap />} />} />
          <Route path="/bulk-ap-tagging" element={<BetaRoute element={<BulkAPTagging />} />} />
          <Route path="/cloudpath-import" element={<ProtectedRoute element={<CloudpathImport />} />} />
          <Route path="/dpsk-orchestrator" element={<BetaRoute element={<DPSKOrchestrator />} />} />
          <Route path="/sz-audit" element={<ProtectedRoute element={<SZAudit />} />} />
          <Route path="/migration-dashboard" element={<ProtectedRoute element={<MigrationDashboard />} />} />
          <Route path="/danger-zone" element={<BetaRoute element={<DangerZone />} />} />
          <Route path="/fileshare" element={<ProtectedRoute element={<FilesharePage />} />} />
          <Route path="/fileshare/admin" element={<AdminRoute element={<FileshareAdmin />} />} />
          <Route path="/fileshare/:folderSlug/*" element={<ProtectedRoute element={<FolderView />} />} />
          <Route path="/jobs" element={<ProtectedRoute element={<JobList />} />} />
          <Route path="/jobs/:jobId" element={<ProtectedRoute element={<JobMonitor />} />} />
          <Route path="/migrate" element={<ProtectedRoute element={<MigrateR1ToR1 />} />} />
          <Route path="/migrate-sz-to-r1" element={<ProtectedRoute element={<MigrateSzToR1 />} />} />
          <Route path="/migrate-sz-config" element={<AlphaRoute element={<MigrateSzToR1Config />} />} />
          <Route path="/migration-audit" element={<AlphaRoute element={<MigrationAudit />} />} />
          <Route path="/data-studio-export" element={<AlphaRoute element={<DataStudioExport />} />} />
          <Route path="/dfs-blacklist" element={<AlphaRoute element={<DfsBlacklist />} />} />
          <Route path="/snapshot" element={<Navigate to="/r1-details" />} />
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
