import { useState } from "react";
import { useNavigate } from "react-router-dom";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const NewProposalForm = () => {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [budget, setBudget] = useState("");
  const [location, setLocation] = useState("");
  const [deadline, setDeadline] = useState("");

  const navigate = useNavigate(); // Hook for redirection

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
  
    const response = await fetch(`${API_BASE_URL}/proposals/new`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title,
        description,
        budget: parseFloat(budget),
        location,
        deadline,
      }),
    });
  
    const result = await response.json();
    
    if (response.ok) {
      alert("Proposal created successfully!");
      setTitle("");
      setDescription("");
      setBudget("");
      setLocation("");
      setDeadline("");
      navigate("/proposals"); // Redirect to proposals page
    } else {
      alert(`Error: ${result.detail || "Failed to create proposal"}`);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="p-4 border rounded-lg shadow-md bg-white max-w-lg mx-auto">
      <h2 className="text-lg font-bold mb-4">New Proposal</h2>

      {/* Title Input */}
      <input 
        type="text" 
        placeholder="Title" 
        value={title} 
        onChange={(e) => setTitle(e.target.value)} 
        className="w-full p-2 border rounded mt-2" 
        required 
      />

      {/* Description Input */}
      <textarea 
        placeholder="Description" 
        value={description} 
        onChange={(e) => setDescription(e.target.value)} 
        className="w-full p-2 border rounded mt-2" 
        required 
      />

      {/* Budget Input */}
      <input 
        type="number" 
        placeholder="Budget" 
        value={budget} 
        onChange={(e) => setBudget(e.target.value)} 
        className="w-full p-2 border rounded mt-2" 
        min="0" 
        required 
      />

      {/* Location Input */}
      <input 
        type="text" 
        placeholder="Location" 
        value={location} 
        onChange={(e) => setLocation(e.target.value)} 
        className="w-full p-2 border rounded mt-2" 
        required 
      />

      {/* Deadline Input */}
      <input 
        type="date" 
        value={deadline} 
        onChange={(e) => setDeadline(e.target.value)} 
        className="w-full p-2 border rounded mt-2" 
        required 
      />

      {/* Submit Button */}
      <button 
        type="submit" 
        className="mt-4 px-4 py-2 bg-blue-500 text-white rounded shadow-md hover:bg-blue-600 transition"
      >
        Create Proposal
      </button>
    </form>
  );
};

export default NewProposalForm;
