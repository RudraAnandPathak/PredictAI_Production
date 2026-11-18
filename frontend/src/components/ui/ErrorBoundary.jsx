import { Component } from 'react';
import { ErrorState } from './ErrorState.jsx';

/** Catches rendering errors so one broken area never blanks the whole shell. */
export class ErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('PredictAI UI error:', error, info?.componentStack);
  }

  reset = () => this.setState({ error: null });

  render() {
    if (this.state.error) {
      return (
        <ErrorState
          title="Something went wrong"
          message="This part of the page could not be shown. Nothing you stored has been changed."
          onRetry={this.reset}
        />
      );
    }
    return this.props.children;
  }
}
