"use client";

import { useState } from "react";
import { loginWithApiKey } from "@/lib/auth";
import { motion } from "framer-motion";
import { KeyRound, User, Loader2, AlertCircle } from "lucide-react";

export default function LoginPage() {
  const [apiKey, setApiKey] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    if (!apiKey.trim()) return;
    setLoading(true);
    setError("");

    const result = await loginWithApiKey(apiKey.trim(), name.trim() || undefined);
    if (result.authenticated) {
      window.location.href = "/";
    } else {
      setError("Invalid API key. Check your key and try again.");
    }
    setLoading(false);
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-neutral-950">
      <motion.form
        initial={{ opacity: 0, y: 20, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.3 }}
        onSubmit={handleLogin}
        className="w-full max-w-sm space-y-4 rounded-xl border border-neutral-800 bg-neutral-900 p-6 shadow-2xl shadow-black/20"
      >
        <div className="text-center">
          <motion.div
            initial={{ scale: 0.8 }}
            animate={{ scale: 1 }}
            transition={{ delay: 0.1, type: "spring", stiffness: 200 }}
            className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-blue-600/20"
          >
            <KeyRound size={24} className="text-blue-400" />
          </motion.div>
          <h1 className="text-lg font-semibold text-neutral-100">Grabon Intel</h1>
          <p className="text-xs text-neutral-500">Sign in to access lead intelligence</p>
        </div>

        <div>
          <label className="mb-1 flex items-center gap-1.5 text-xs text-neutral-400">
            <User size={12} />
            Your Name
          </label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Santhosh"
            className="w-full rounded-lg border border-neutral-700 bg-neutral-950 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-blue-600/50 focus:ring-1 focus:ring-blue-600/20 transition-all"
          />
        </div>

        <div>
          <label className="mb-1 flex items-center gap-1.5 text-xs text-neutral-400">
            <KeyRound size={12} />
            API Key
          </label>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="Enter your API key"
            className="w-full rounded-lg border border-neutral-700 bg-neutral-950 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-blue-600/50 focus:ring-1 focus:ring-blue-600/20 transition-all"
            required
          />
        </div>

        {error && (
          <motion.p
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex items-center gap-1.5 text-xs text-rose-400"
          >
            <AlertCircle size={12} />
            {error}
          </motion.p>
        )}

        <motion.button
          whileTap={{ scale: 0.98 }}
          type="submit"
          disabled={loading}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 py-2.5 text-sm font-medium text-white disabled:opacity-50 transition-colors hover:bg-blue-500"
        >
          {loading ? (
            <>
              <Loader2 size={14} className="animate-spin" />
              Signing in...
            </>
          ) : (
            "Sign In"
          )}
        </motion.button>
      </motion.form>
    </main>
  );
}
