import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const NewBidForm = () => {
  const navigate = useNavigate();
  const { userId } = useAuth(); // Fetch authenticated user
  const [searchParams] = useSearchParams();
  const proposalId = Number(searchParams.get("proposalId"));

  const [amount, setAmount] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!proposalId || !userId) {
      setError("Invalid request. Proposal or user missing.");
      return;
    }

    const newBid = {
      proposal_id: proposalId,
      bidder_id: userId, // User ID from authentication
      amount: parseFloat(amount),
      message,
      submitted_at: new Date().toISOString(),
    };

    try {
      const response = await fetch(`${API_BASE_URL}/bids/new`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(newBid),
      });

      if (!response.ok) throw new Error("Failed to submit bid");

      navigate(`/proposals/${proposalId}`); // Redirect back to proposal
    } catch (error) {
      setError(error.message);
    }
  };

  return (
    <div className="max-w-lg mx-auto p-6 bg-white shadow-md rounded-lg">
      <h2 className="text-2xl font-bold mb-4">Submit a Bid</h2>

      {error && <p className="text-red-500">{error}</p>}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium">Amount ($)</label>
          <input
            type="number"
            step="0.01"
            className="w-full px-3 py-2 border rounded-md"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            required
          />
        </div>

        <div>
          <label className="block text-sm font-medium">Message</label>
          <textarea
            className="w-full px-3 py-2 border rounded-md"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            rows={3}
            required
          />
        </div>

        <button
          type="submit"
          className="w-full bg-blue-500 text-white px-4 py-2 rounded-md shadow hover:bg-blue-600"
        >
          Submit Bid
        </button>
      </form>
    </div>
  );
};

export default NewBidForm;
