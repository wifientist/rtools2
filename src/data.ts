import type { Proposal, Bid } from "@/types";

export const proposals: Proposal[] = [
  {
    id: 1,
    title: "Wireless Network Installation",
    description: "Need a full install for a 20,000 sqft office.",
    budget: 5000,
    location: "San Francisco, CA",
    deadline: "2025-04-01",
    createdBy: "JohnDoe",
  },
  {
    id: 2,
    title: "Fiber Cable Installation",
    description: "Looking for a crew to run 10,000ft of fiber.",
    budget: 15000,
    location: "Los Angeles, CA",
    deadline: "2025-05-15",
    createdBy: "JaneSmith",
  },
];

export const bids: Bid[] = [
  {
    id: 1,
    proposalId: 1,
    bidder: "XYZ Cable Crew",
    amount: 4800,
    message: "We can complete this in 5 days.",
    submittedAt: "2025-03-10",
  },
  {
    id: 2,
    proposalId: 1,
    bidder: "NetworkPros LLC",
    amount: 4500,
    message: "Our team has extensive experience with this.",
    submittedAt: "2025-03-11",
  },
];
