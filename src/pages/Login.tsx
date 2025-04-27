import { useState } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const Login = () => {
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
            const response = await fetch(`${API_BASE_URL}/auth/request-otp`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email }),
            });

            if (!response.ok) {
                const errorData = await response.json();
                console.error(errorData.error); // always exists now
                throw new Error(errorData.error || "Unknown error");
            }

            setOtpSent(true);  // Move to OTP input step
            
        } catch (err: any) {
            setError(err.message);
        }
    };

    const handleLoginWithOtp = async (e: React.FormEvent) => {
        e.preventDefault();
        setError("");
        setSuccess(false);

        try {
            const response = await fetch(`${API_BASE_URL}/auth/login-otp`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, otp_code: otp }),
            });

            if (!response.ok) {
                throw new Error("Invalid OTP or login failed.");
            }

            setSuccess(true);
            window.location.href = "/profile"; // Redirect after login
        } catch (err: any) {
            setError(err.message);
        }
    };

    return (
        <div className="flex flex-col items-center justify-center min-h-screen">
            <h2 className="text-2xl font-bold mb-4">{otpSent ? "Enter Your OTP" : "Login"}</h2>

            {/* Step 1: Request OTP */}
            {!otpSent && (
                <form onSubmit={handleRequestOtp} className="bg-gray-100 p-6 rounded shadow-md w-80">
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

            {/* Step 2: Login with OTP */}
            {otpSent && (
                <form onSubmit={handleLoginWithOtp} className="bg-gray-100 p-6 rounded shadow-md w-80">
                    {error && <p className="text-red-500">{error}</p>}
                    {success && <p className="text-green-500">Login successful! Redirecting...</p>}
                    <input
                        type="text"
                        placeholder="Enter OTP"
                        value={otp}
                        onChange={(e) => setOtp(e.target.value)}
                        className="block w-full p-2 mb-2 border rounded"
                        required
                    />
                    <button type="submit" className="bg-green-500 text-white p-2 rounded w-full">
                        Verify and Login
                    </button>
                </form>
            )}
        </div>
    );
};

export default Login;
