// Agent Mesh uses username-based authentication — no account creation needed.
// Redirect to the login page which shows the demo user cards.
import { Navigate } from "react-router-dom";

export function SignupPage() {
  return <Navigate to="/login" replace />;
}
