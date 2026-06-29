import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  label?: string
}

interface State {
  error: Error | null
}

/**
 * Local error boundary to keep one panel crash from blanking the whole UI.
 * Logs to the console; renders a small fallback card with a retry button.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[${this.props.label || 'panel'}] crashed:`, error, info.componentStack)
  }

  reset = () => this.setState({ error: null })

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="error-boundary">
        <h3>Đã xảy ra lỗi khi hiển thị phần này</h3>
        <div className="err-msg">{this.state.error.message}</div>
        <button className="btn btn-sm" onClick={this.reset}>
          Thử lại
        </button>
      </div>
    )
  }
}
