import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { ErrorState, ReplayBadge, SeverityChip, VerdictBadge } from "./ui";

describe("status components", () => {
  it("verdicts carry a text symbol, never colour alone", () => {
    render(<VerdictBadge verdict="CONFIRMED" />);
    expect(screen.getByText("■ CONFIRMED")).toBeInTheDocument();
  });

  it("missing verdict is shown as pending, not as a pass", () => {
    render(<VerdictBadge verdict={null} />);
    expect(screen.getByText(/pending/)).toBeInTheDocument();
  });

  it("inconclusive shows its reason", () => {
    render(<VerdictBadge verdict="INCONCLUSIVE" reason="timeout" />);
    expect(screen.getByText(/timeout/)).toBeInTheDocument();
  });

  it("severity renders as text", () => {
    render(<SeverityChip severity="critical" />);
    expect(screen.getByText("CRITICAL")).toBeInTheDocument();
  });

  it("errors show code and an actionable hint", () => {
    render(<ErrorState error={new ApiError(503, "sandbox_unavailable", "Docker is not reachable.")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("sandbox_unavailable");
    expect(screen.getByRole("alert")).toHaveTextContent("Nothing was executed");
  });

  it("labels simulated replays and nothing else", () => {
    const { container, rerender } = render(<ReplayBadge runId="run_01ABC" />);
    expect(container).toBeEmptyDOMElement();
    rerender(<ReplayBadge runId="replay_run_01ABC_lz1" />);
    expect(screen.getByText("SIMULATED REPLAY")).toBeInTheDocument();
  });
});
