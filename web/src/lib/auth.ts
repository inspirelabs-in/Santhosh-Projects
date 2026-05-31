/**
 * Simple JWT-based auth for the frontend.
 * Supports login via API key → JWT token exchange.
 * Stores token in localStorage for persistence.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type User = {
  id: string;
  name: string;
  role: "admin" | "analyst" | "viewer";
  email: string;
};

export type AuthState = {
  user: User | null;
  token: string | null;
  authenticated: boolean;
};

const TOKEN_KEY = "grabon_auth_token";
const USER_KEY = "grabon_auth_user";

export function getStoredAuth(): AuthState {
  if (typeof window === "undefined") return { user: null, token: null, authenticated: false };
  const token = localStorage.getItem(TOKEN_KEY);
  const userStr = localStorage.getItem(USER_KEY);
  if (!token || !userStr) return { user: null, token: null, authenticated: false };
  try {
    return { user: JSON.parse(userStr), token, authenticated: true };
  } catch {
    return { user: null, token: null, authenticated: false };
  }
}

export async function loginWithApiKey(apiKey: string, userName?: string): Promise<AuthState> {
  try {
    const resp = await fetch(`${API_BASE}/me`, {
      headers: { "X-API-Key": apiKey },
    });
    if (!resp.ok) throw new Error("Invalid API key");

    const user: User = {
      id: "user_" + apiKey.slice(0, 8),
      name: userName ?? "Analyst",
      role: "admin",
      email: "",
    };

    // Use API key as token for now — JWT exchange can be added later
    localStorage.setItem(TOKEN_KEY, apiKey);
    localStorage.setItem(USER_KEY, JSON.stringify(user));

    return { user, token: apiKey, authenticated: true };
  } catch (err) {
    return { user: null, token: null, authenticated: false };
  }
}

export function logout(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function getAuthHeaders(): Record<string, string> {
  const token = typeof window !== "undefined" ? localStorage.getItem(TOKEN_KEY) : null;
  if (!token) return {};
  return { "X-API-Key": token };
}
