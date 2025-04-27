import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import { useState, useEffect } from "react";

import Navbar from "@/components/Navbar";
import Home from "@/pages/Home";
import Status from "@/pages/Status";
import Proposals from "@/pages/Proposals";
import NewProposalForm from "@/components/NewProposalForm";
import NewBidForm from "@/components/NewBidForm";
import Login from "@/pages/Login";
import Signup from "@/pages/Signup";
import Profile from "@/pages/Profile";
import Admin from "./pages/Admin";
import ProtectedRoute from "@/components/ProtectedRoute";
import AdminRoute from "@/components/AdminRoute";
import { AuthProvider } from "@/context/AuthContext";

const App = () => {
  
  return (
    <AuthProvider>
    <Router>
      <Navbar />
      <div className="container mx-auto p-6">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/status" element={<Status />} />
          <Route path="/login" element={<Login />} />
          <Route path="/signup" element={<Signup />} />
          <Route path="/profile" element={<ProtectedRoute element={<Profile />} />} />
          <Route path="/admin" element={<AdminRoute element={<Admin />} />} />
          <Route path="/proposals" element={<ProtectedRoute element={<Proposals />} />} />
          <Route path="/proposals/new" element={<ProtectedRoute element={<NewProposalForm />} />} />
          <Route path="/bids/new" element={<NewBidForm />} />
          <Route path="*" element={<Navigate to="/" />} /> 
        </Routes>
      </div>
    </Router>
    </AuthProvider>
  );
};

export default App;
