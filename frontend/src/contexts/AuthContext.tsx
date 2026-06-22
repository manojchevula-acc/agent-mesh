import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { loginUser } from "@/api/mesh";
import type { MeshUser } from "@/types/mesh";

// Extend the base User type with agent-mesh fields (role, display_name).
export interface User extends MeshUser {
  name: string;   // alias for display_name, kept for AppLayout/UserMenu compatibility
  email: string;  // derived as username@mesh.local for compatibility
}

interface AuthContextValue {
  user: User | null;
  isAuthenticated: boolean;
  login: (username: string) => Promise<void>;
  logout: () => void;
}

const SESSION_KEY = "agent-mesh-session";

const AuthContext = createContext<AuthContextValue | null>(null);

function readSession(): User | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as User) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(readSession);

  const persistSession = useCallback((u: User | null) => {
    if (u) localStorage.setItem(SESSION_KEY, JSON.stringify(u));
    else localStorage.removeItem(SESSION_KEY);
    setUser(u);
  }, []);

  // Re-validate session on mount against the live API (best-effort).
  useEffect(() => {
    const stored = readSession();
    if (stored) {
      loginUser(stored.username)
        .then((meshUser) => {
          persistSession({
            ...meshUser,
            name: meshUser.display_name,
            email: `${meshUser.username}@mesh.local`,
          });
        })
        .catch(() => {
          // API unreachable — keep stored session so the UI still works offline.
        });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const login = useCallback(
    async (username: string) => {
      const meshUser = await loginUser(username.trim() || "bob");
      persistSession({
        ...meshUser,
        name: meshUser.display_name,
        email: `${meshUser.username}@mesh.local`,
      });
    },
    [persistSession],
  );

  const logout = useCallback(() => persistSession(null), [persistSession]);

  const value = useMemo<AuthContextValue>(
    () => ({ user, isAuthenticated: Boolean(user), login, logout }),
    [user, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
