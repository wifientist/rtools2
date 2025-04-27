import { useState } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const Login = () => {
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [error, setError] = useState("");

    const handleLogin = async (e: React.FormEvent) => {
        e.preventDefault();
        setError(""); // Reset error state

        const formData = new URLSearchParams();
        formData.append("username", email);
        formData.append("password", password);

        try {
            const response = await fetch(`${API_BASE_URL}/auth/token`, {
                method: "POST",
                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                body: formData.toString(),
            });

            if (!response.ok) {
                throw new Error("Invalid email or password");
            }

            const data = await response.json();

            localStorage.setItem("access_token", data.access_token);
            localStorage.setItem("user_role", data.role);

            window.location.href = "/profile"; // Redirect after login
        } catch (err: any) {
            setError(err.message);
        }
    };

    return (
        <div className="flex flex-col items-center justify-center min-h-screen">
            <h2 className="text-2xl font-bold mb-4">Login</h2>
            <form onSubmit={handleLogin} className="bg-gray-100 p-6 rounded shadow-md w-80">
                {error && <p className="text-red-500">{error}</p>}
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
                <button type="submit" className="bg-blue-500 text-white p-2 rounded w-full">Login</button>
            </form>
        </div>
    );
};

export default Login;
