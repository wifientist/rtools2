import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import { useState, useEffect } from "react";

import Navbar from "@/components/Navbar";
import ProtectedRoute from "@/components/ProtectedRoute";
import AdminRoute from "@/components/AdminRoute";

import { AuthProvider } from "@/context/AuthContext";

import Home from "@/pages/Home";
import About from "@/pages/About";
import Status from "@/pages/Status";
import Login from "@/pages/Login";
import Signup from "@/pages/Signup";
import Profile from "@/pages/Profile";
import Admin from "@/pages/Admin";
import Tenants from "@/pages/Tenants";
import Diff from "@/pages/Diff";
import Layout from "@/components/Layout";
import Snapshot from "@/pages/Snapshot";

const App = () => {
  
  return (
    <AuthProvider>
    <Router>
      <Layout>
        {/*<div className="container mx-auto p-6">*/}
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/about" element={<About />} />
          <Route path="/status" element={<Status />} />
          <Route path="/login" element={<Login />} />
          <Route path="/signup" element={<Signup />} />
          <Route path="/profile" element={<ProtectedRoute element={<Profile />} />} />
          <Route path="/admin" element={<AdminRoute element={<Admin />} />} />
          <Route path="/tenants" element={<Tenants />} />
          <Route path="/diff" element={<Diff />} />
          <Route path="/snapshot" element={<Snapshot />} />
          <Route path="*" element={<Navigate to="/" />} /> 
        </Routes>
      {/*</div>*/}
      </Layout>
    </Router>
    </AuthProvider>
  );
};

export default App;
