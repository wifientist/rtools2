import { useState } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const Signup = () => {
    const [email, setEmail] = useState("");
    const [otp, setOtp] = useState("");
    const [error, setError] = useState("");
    const [success, setSuccess] = useState(false);
    const [otpSent, setOtpSent] = useState(false);

    const handleRequestOtp = async (e: React.FormEvent) => {
        e.preventDefault();
        setError("");
        setSuccess(false);

        try {
            const response = await fetch(`${API_BASE_URL}/auth/signup-request-otp`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email }),
            });

            if (!response.ok) {
                throw new Error("Failed to request OTP. Email may already exist.");
            }

            setOtpSent(true);  // Move to OTP input step
        } catch (err: any) {
            setError(err.message);
        }
    };

    const handleVerifyOtp = async (e: React.FormEvent) => {
        e.preventDefault();
        setError("");
        setSuccess(false);

        try {
            const response = await fetch(`${API_BASE_URL}/auth/signup-verify-otp`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, otp_code: otp }),
            });

            if (!response.ok) {
                if (response.status === 403) {
                    const errorData = await response.json();
                    // Show domain approval message if that's the specific reason
                    if (errorData.detail && errorData.detail.includes("approved")) {
                        throw new Error(errorData.detail);
                    }
                }
                throw new Error("OTP verification failed.");
            }

            setSuccess(true);
            window.location.href = "/profile"; // Redirect after signup/login
        } catch (err: any) {
            setError(err.message);
        }
    };

    return (
        <div className="flex flex-col items-center justify-center min-h-screen">
            <h2 className="text-2xl font-bold mb-4">{otpSent ? "Verify Your Email" : "Sign Up"}</h2>

            {/* Step 1: Request OTP */}
            {!otpSent && (
                <form onSubmit={handleRequestOtp} className="bg-gray-100 p-6 rounded shadow-md w-80">
                    <div className="bg-blue-50 border border-blue-200 text-blue-800 p-3 rounded mb-4 text-sm">
                        <p className="font-semibold mb-1">Company Domain Approval Required</p>
                        <p>Only users with approved company email domains can sign up. Contact an administrator if your domain needs approval.</p>
                    </div>
                    {error && <p className="text-red-500">{error}</p>}
                    <input
                        type="email"
                        placeholder="Email"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        className="block w-full p-2 mb-2 border rounded"
                        required
                    />
                    <button type="submit" className="bg-blue-500 text-white p-2 rounded w-full">
                        Request OTP
                    </button>
                </form>
            )}

            {/* Step 2: Verify OTP */}
            {otpSent && (
                <form onSubmit={handleVerifyOtp} className="bg-gray-100 p-6 rounded shadow-md w-80">
                    {error && <p className="text-red-500">{error}</p>}
                    {success && <p className="text-green-500">Signup successful! Redirecting...</p>}
                    <input
                        type="text"
                        placeholder="Enter OTP"
                        value={otp}
                        onChange={(e) => setOtp(e.target.value)}
                        className="block w-full p-2 mb-2 border rounded"
                        required
                    />
                    <button type="submit" className="bg-green-500 text-white p-2 rounded w-full">
                        Verify and Create Account
                    </button>
                </form>
            )}

            <p className="mt-4">
                Already have an account?{" "}
                <a href="/login" className="text-blue-500">Log in</a>
            </p>
        </div>
    );
};

export default Signup;
