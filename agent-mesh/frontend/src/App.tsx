import { Navigate, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "@/components/auth/ProtectedRoute";
import { AppLayout } from "@/components/layout/AppLayout";
import { HomePage } from "@/pages/HomePage";
import { LoginPage } from "@/pages/LoginPage";
import { SignupPage } from "@/pages/SignupPage";
import ChatPage from "@/pages/ChatPage";
import ApprovalPage from "@/pages/ApprovalPage";
import FeedbackDashboardPage from "@/pages/FeedbackDashboardPage";
import AuditDashboardPage from "@/pages/AuditDashboardPage";
import ConversationsDashboardPage from "@/pages/ConversationsDashboardPage";
import LogsDashboardPage from "@/pages/LogsDashboardPage";
import RequestActivityPage from "@/pages/RequestActivityPage";
import { EmptyState } from "@/components/ui/EmptyState";

export default function App() {
  return (
    <Routes>
      {/* Public */}
      <Route path="/" element={<HomePage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      {/* Standalone approval page — accessible without login (future: email link) */}
      <Route path="/approval/:id" element={<ApprovalPage />} />

      {/* Authenticated app */}
      <Route element={<ProtectedRoute />}>
        <Route path="/app" element={<AppLayout />}>
          <Route index element={<Navigate to="/app/chat" replace />} />
          <Route path="chat" element={<ChatPage />} />
          <Route path="activity" element={<RequestActivityPage />} />
          <Route path="feedback" element={<FeedbackDashboardPage />} />
          <Route path="audit" element={<AuditDashboardPage />} />
          <Route path="conversations" element={<ConversationsDashboardPage />} />
          <Route path="logs" element={<LogsDashboardPage />} />
        </Route>
      </Route>

      <Route
        path="*"
        element={
          <div className="flex min-h-screen items-center justify-center bg-canvas p-8">
            <EmptyState
              title="Page not found"
              description="The page you're looking for doesn't exist."
            />
          </div>
        }
      />
    </Routes>
  );
}
