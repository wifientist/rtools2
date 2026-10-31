import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import { useState, useEffect, lazy, Suspense } from "react";

import Navbar from "@/components/Navbar";
import ProtectedRoute from "@/components/ProtectedRoute";
import AdminRoute from "@/components/AdminRoute";
import BetaRoute from "@/components/BetaRoute";
import AlphaRoute from "@/components/AlphaRoute";
import DangerRoute from "@/components/DangerRoute";

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
import R1CallTiming from "@/pages/R1CallTiming";
import Option43Calculator from "@/pages/Option43Calculator";
import CompanyManager from "@/components/CompanyManager";
import Users from "@/pages/Users";
import PerUnitSSID from "@/pages/PerUnitSSID";
import APPortConfig from "@/pages/APPortConfig";
import APRename from "@/pages/APRename";
import BulkWlanEdit from "@/pages/BulkWlanEdit";
import BulkWlanDelete from "@/pages/BulkWlanDelete";
import MigrationDashboard from "@/pages/MigrationDashboard";
import MigrationTracker from "@/pages/MigrationTracker";
import SpeedExplainer from "@/pages/SpeedExplainer";
import RoamingExplainer from "@/pages/RoamingExplainer";
import FirmwareMatrix from "@/pages/FirmwareMatrix";
import CloudpathImport from "@/pages/CloudpathImport";
import WlanActivator from "@/pages/WlanActivator";
import WlanVlan from "@/pages/WlanVlan";
import WlanRename from "@/pages/WlanRename";
import WlanGrabber from "@/pages/WlanGrabber";
import ApgCleanup from "@/pages/ApgCleanup";
import VenueCheckup from "@/pages/VenueCheckup";
import MigrateSzToR1Config from "@/pages/MigrateSzToR1Config";
import MigrationAudit from "@/pages/MigrationAudit";
import JobMonitor from "@/pages/JobMonitor";
import JobList from "@/pages/JobList";
import SZAudit from "@/pages/SZAudit";
import BulkAPTagging from "@/pages/BulkAPTagging";
import DangerZone from "@/pages/DangerZone";
import DataStudioExport from "@/pages/DataStudioExport";
import Maps from "@/pages/Maps";
import WiredWiz from "@/pages/WiredWiz";
// Lazy, unlike every other page here: React Flow + dagre are ~2 MB, and users
// who never open the map should not pay for them. Deliberate inconsistency.
const Topology = lazy(() => import("@/pages/Topology"));
import PISR from "@/pages/PISR";
import TemplateTool from "@/pages/TemplateTool";
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
          <Route path="/bulk-wlan-delete" element={<BetaRoute element={<BulkWlanDelete />} />} />
          <Route path="/bulk-ap-tagging" element={<ProtectedRoute element={<BulkAPTagging />} />} />
          <Route path="/cloudpath-import" element={<ProtectedRoute element={<CloudpathImport />} />} />
          {/* AP Regroup folded into APG Cleanup (2026-10-22); page kept in src/pages/APRegroup.tsx */}
          <Route path="/ap-regroup" element={<Navigate to="/apg-cleanup" />} />
          <Route path="/wlan-activator" element={<ProtectedRoute element={<WlanActivator />} />} />
          <Route path="/sz-audit" element={<ProtectedRoute element={<SZAudit />} />} />
          <Route path="/migration-dashboard" element={<ProtectedRoute element={<MigrationDashboard />} />} />
          <Route path="/danger-zone" element={<DangerRoute element={<DangerZone />} />} />
          <Route path="/fileshare" element={<ProtectedRoute element={<FilesharePage />} />} />
          <Route path="/fileshare/admin" element={<AdminRoute element={<FileshareAdmin />} />} />
          <Route path="/fileshare/:folderSlug/*" element={<ProtectedRoute element={<FolderView />} />} />
          <Route path="/jobs" element={<ProtectedRoute element={<JobList />} />} />
          <Route path="/jobs/:jobId" element={<ProtectedRoute element={<JobMonitor />} />} />
          <Route path="/migrate" element={<ProtectedRoute element={<MigrateR1ToR1 />} />} />
          <Route path="/migrate-sz-to-r1" element={<ProtectedRoute element={<MigrateSzToR1 />} />} />
          <Route path="/migrate-sz-config" element={<BetaRoute element={<MigrateSzToR1Config />} />} />
          <Route path="/migration-audit" element={<ProtectedRoute element={<MigrationAudit />} />} />
          <Route path="/wlan-vlan" element={<ProtectedRoute element={<WlanVlan />} />} />
          <Route path="/wlan-rename" element={<AlphaRoute element={<WlanRename />} />} />
          <Route path="/wlan-grabber" element={<ProtectedRoute element={<WlanGrabber />} />} />
          <Route path="/apg-cleanup" element={<ProtectedRoute element={<ApgCleanup />} />} />
          <Route path="/venue-checkup" element={<BetaRoute element={<VenueCheckup />} />} />
          <Route path="/data-studio-export" element={<AlphaRoute element={<DataStudioExport />} />} />
          <Route path="/maps" element={<AlphaRoute element={<Maps />} />} />
          <Route path="/wiredwiz" element={<AlphaRoute element={<WiredWiz />} />} />
          <Route
            path="/topology"
            element={
              <AlphaRoute
                element={
                  <Suspense
                    fallback={
                      <div className="flex items-center justify-center p-12">
                        <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-blue-600" />
                      </div>
                    }
                  >
                    <Topology />
                  </Suspense>
                }
              />
            }
          />
          <Route path="/pisr" element={<AlphaRoute element={<PISR />} />} />
          <Route path="/tracker" element={<AlphaRoute element={<MigrationTracker />} />} />
          <Route path="/template-tool" element={<BetaRoute element={<TemplateTool />} />} />
          <Route path="/snapshot" element={<Navigate to="/r1-details" />} />
          <Route path="/testcalls" element={<AdminRoute element={<TestCalls />} />} />
          <Route path="/r1-call-timing" element={<AdminRoute element={<R1CallTiming />} />} />
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
