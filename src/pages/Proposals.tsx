import { useEffect, useState, useContext } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";

//const API_BASE_URL = "/api";
//const API_BASE_URL = process.env.API_BASE_URL;
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const Proposals = () => {

  const { isAuthenticated, userRole } = useAuth()

  const [proposals, setProposals] = useState([]);
  const [bids, setBids] = useState({});
  const [selectedProposal, setSelectedProposal] = useState(null);

  useEffect(() => {
    fetch(`${API_BASE_URL}/proposals`)
      .then((res) => res.json())
      .then((data) => setProposals(data))
      .catch((error) => console.error("Error fetching proposals:", error));
  }, []);

  const fetchBids = (proposalId) => {
    if (selectedProposal === proposalId) {
      setSelectedProposal(null);
      return;
    }
    fetch(`${API_BASE_URL}/bids/${proposalId}`)
      .then((res) => res.json())
      .then((data) => {
        setBids((prevBids) => ({ ...prevBids, [proposalId]: data }));
        setSelectedProposal(proposalId);
      })
      .catch((error) => console.error("Error fetching bids:", error));
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-2xl font-bold">Proposals</h2>
        {isAuthenticated && userRole === "admin" && (
        <Link
          to="/proposals/new"
          className="bg-green-500 text-white px-4 py-2 rounded-md shadow hover:bg-green-600"
        >
          + Create Proposal
        </Link>
        )}
      </div>

      {proposals.length === 0 ? (
        <p className="text-gray-600">No proposals found.</p>
      ) : (
        <div className="bg-white shadow-md rounded-lg overflow-hidden">
          {/* Header Row - Visible on md+ screens */}
          <div className="hidden md:grid grid-cols-[2fr_3fr_2fr_2fr_2fr_1fr] bg-gray-200 p-3 font-semibold text-gray-700">
            <div>Title</div>
            <div>Description</div>
            <div>Location</div>
            <div>Deadline</div>
            <div>Budget</div>
            <div>Actions</div>
          </div>

          {/* Proposal List */}
          <div className="divide-y">
            {proposals.map((proposal) => (
              <div
                key={proposal.id}
                className="p-4 flex flex-col md:grid md:grid-cols-[2fr_3fr_2fr_2fr_2fr_1fr] items-center gap-3"
              >
                {/* Title */}
                <div className="w-full md:w-auto">
                  <span className="md:hidden font-semibold text-gray-700">Title: </span>
                  {proposal.title}
                </div>

                {/* Description */}
                <div className="w-full md:w-auto">
                  <span className="md:hidden font-semibold text-gray-700">Description: </span>
                  {proposal.description}
                </div>

                {/* Location */}
                <div className="w-full md:w-auto">
                  <span className="md:hidden font-semibold text-gray-700">Location: </span>
                  {proposal.location}
                </div>

                {/* Deadline */}
                <div className="w-full md:w-auto">
                  <span className="md:hidden font-semibold text-gray-700">Deadline: </span>
                  {new Date(proposal.deadline).toLocaleDateString()}
                </div>

                {/* Budget */}
                <div className="w-full md:w-auto">
                  <span className="md:hidden font-semibold text-gray-700">Budget: </span>
                  ${proposal.budget}
                </div>

                {/* Actions */}
                <div className="w-full md:w-auto flex justify-end">
                  {isAuthenticated && userRole === "admin" && (
                    <button
                      className="bg-blue-500 text-white px-4 py-2 rounded-md shadow hover:bg-blue-600 transition"
                      onClick={() => fetchBids(proposal.id)}
                    >
                      {selectedProposal === proposal.id ? "Hide Bids" : "View Bids"}
                    </button>
                  )}
                  {isAuthenticated && userRole !== "admin" && (
                    <Link
                      to={`/bids/new?proposalId=${proposal.id}`}
                      className="bg-yellow-500 text-white px-4 py-2 rounded-md shadow hover:bg-yellow-600 transition"
                    >
                      Create Bid
                    </Link>
                  )}
                </div>

                {/* Bids Drawer */}
                <div className={`col-span-6 w-full transition-all ${selectedProposal === proposal.id ? "max-h-96" : "max-h-0 overflow-hidden"}`}>
                {isAuthenticated && userRole === "admin" && selectedProposal === proposal.id && (
                    <div className="mt-4 border-t pt-4">
                      <h4 className="font-semibold text-gray-800">Bids:</h4>
                      {bids[proposal.id]?.length === 0 ? (
                        <p className="text-gray-500">No bids yet.</p>
                      ) : (
                        <div className="space-y-2">
                          {bids[proposal.id].map((bid) => (
                            <div
                              key={bid.id}
                              className="flex justify-between items-center bg-gray-100 p-2 rounded-md shadow-sm"
                            >
                              <span className="font-medium text-gray-700">Bidder: {bid.bidder_id}</span>
                              <span className="text-gray-600">${bid.amount.toFixed(2)}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default Proposals;
