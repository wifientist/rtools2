import { useState } from "react";

//const API_BASE_URL = process.env.API_BASE_URL;
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const Signup = () => {
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [error, setError] = useState("");
    const [success, setSuccess] = useState(false);

    const handleSignup = async (e: React.FormEvent) => {
        e.preventDefault();
        setError("");
        setSuccess(false);

        try {
            const response = await fetch(`${API_BASE_URL}/auth/signup`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, password }),
            });

            if (!response.ok) {
                throw new Error("Signup failed. Email may already be in use.");
            }

            const data = await response.json();
            localStorage.setItem("access_token", data.access_token);
            setSuccess(true);
            window.location.href = "/profile"; // Redirect after signup
        } catch (err: any) {
            setError(err.message);
        }
    };

    return (
        <div className="flex flex-col items-center justify-center min-h-screen">
            <h2 className="text-2xl font-bold mb-4">Sign Up</h2>
            <form onSubmit={handleSignup} className="bg-gray-100 p-6 rounded shadow-md w-80">
                {error && <p className="text-red-500">{error}</p>}
                {success && <p className="text-green-500">Signup successful! Redirecting...</p>}
                <input
                    type="email"
                    placeholder="Email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="block w-full p-2 mb-2 border rounded"
                    required
                />
                <input
                    type="password"
                    placeholder="Password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="block w-full p-2 mb-2 border rounded"
                    required
                />
                <button type="submit" className="bg-blue-500 text-white p-2 rounded w-full">Sign Up</button>
            </form>
            <p className="mt-4">
                Already have an account? <a href="/login" className="text-blue-500">Log in</a>
            </p>
        </div>
    );
};

export default Signup;
