import { Navigate, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "@/components/auth/ProtectedRoute";
import { AppLayout } from "@/components/layout/AppLayout";
import { HomePage } from "@/pages/HomePage";
import { LoginPage } from "@/pages/LoginPage";
import { SignupPage } from "@/pages/SignupPage";
import { SearchPage } from "@/pages/SearchPage";
import { UploadPage } from "@/pages/UploadPage";
import { EvaluationPage } from "@/pages/EvaluationPage";
import { AdminPage } from "@/pages/AdminPage";
import { EmptyState } from "@/components/ui/EmptyState";

export default function App() {
  return (
    <Routes>
      {/* Public */}
      <Route path="/" element={<HomePage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />

      {/* Authenticated app */}
      <Route element={<ProtectedRoute />}>
        <Route path="/app" element={<AppLayout />}>
          <Route index element={<Navigate to="/app/search" replace />} />
          <Route path="search" element={<SearchPage />} />
          <Route path="upload" element={<UploadPage />} />
          <Route path="evaluation" element={<EvaluationPage />} />
          <Route path="admin" element={<AdminPage />} />
        </Route>
      </Route>

      <Route
        path="*"
        element={
          <div className="flex min-h-screen items-center justify-center bg-canvas p-8">
            <EmptyState
              title="Page not found"
              description="The page you’re looking for doesn’t exist."
            />
          </div>
        }
      />
    </Routes>
  );
}
