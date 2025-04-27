export type Bid = {
    id: number;
    proposalId: number; // Tied to a proposal
    bidder: string; // Name of the bidding company or user
    amount: number;
    message: string;
    submittedAt: string;
  };
  