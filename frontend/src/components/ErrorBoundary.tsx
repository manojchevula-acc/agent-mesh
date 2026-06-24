import { Component, type ErrorInfo, type ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";

interface State {
  error: Error | null;
}

/** Top-level guard so a render error shows a recoverable message, not a blank page. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Unhandled UI error:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="mx-auto max-w-lg p-8">
          <Alert variant="error" title="Something went wrong">
            {this.state.error.message}
          </Alert>
          <div className="mt-4">
            <Button onClick={() => this.setState({ error: null })}>Try again</Button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
