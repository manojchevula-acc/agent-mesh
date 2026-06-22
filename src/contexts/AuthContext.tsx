import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

export interface User {
  name: string;
  email: string;
}

interface AuthContextValue {
  user: User | null;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (name: string, email: string, password: string) => Promise<void>;
  logout: () => void;
}

const SESSION_KEY = "gernas-session";
const USERS_KEY = "gernas-users";

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Client-side mock auth for the POC — credentials live in localStorage only.
 * Swap `login`/`signup` for real API calls when an auth backend exists.
 */
type StoredUser = User & { password: string };

function readUsers(): StoredUser[] {
  try {
    return JSON.parse(localStorage.getItem(USERS_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function readSession(): User | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as User) : null;
  } catch {
    return null;
  }
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(readSession);

  const persistSession = useCallback((u: User | null) => {
    if (u) localStorage.setItem(SESSION_KEY, JSON.stringify(u));
    else localStorage.removeItem(SESSION_KEY);
    setUser(u);
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      await delay(600);
      const found = readUsers().find(
        (u) => u.email.toLowerCase() === email.trim().toLowerCase(),
      );
      // Demo convenience: allow any login if no account was ever created.
      if (!found) {
        persistSession({ name: email.split("@")[0] || "Analyst", email: email.trim() });
        return;
      }
      if (found.password !== password) throw new Error("Incorrect password. Please try again.");
      persistSession({ name: found.name, email: found.email });
    },
    [persistSession],
  );

  const signup = useCallback(
    async (name: string, email: string, password: string) => {
      await delay(700);
      const users = readUsers();
      const normalized = email.trim().toLowerCase();
      if (users.some((u) => u.email.toLowerCase() === normalized)) {
        throw new Error("An account with this email already exists.");
      }
      const newUser: StoredUser = { name: name.trim(), email: email.trim(), password };
      localStorage.setItem(USERS_KEY, JSON.stringify([...users, newUser]));
      persistSession({ name: newUser.name, email: newUser.email });
    },
    [persistSession],
  );

  const logout = useCallback(() => persistSession(null), [persistSession]);

  const value = useMemo<AuthContextValue>(
    () => ({ user, isAuthenticated: Boolean(user), login, signup, logout }),
    [user, login, signup, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
