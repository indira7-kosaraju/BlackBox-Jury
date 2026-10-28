import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { SessionProvider } from "./app/session";
import { ToastProvider } from "./components/ui";

function mount() {
  return render(
    <MemoryRouter>
      <SessionProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
}

describe("App", () => {
  it("asks for a token before showing any data", () => {
    sessionStorage.clear();
    mount();
    expect(screen.getByLabelText("API token")).toBeInTheDocument();
    expect(screen.queryByText("AI Agent Security Command Center")).not.toBeInTheDocument();
  });

  it("demo mode is labelled on every screen and shows recorded data", async () => {
    sessionStorage.clear();
    mount();
    fireEvent.click(screen.getByText(/Explore demo data/));
    expect(await screen.findByText("AI Agent Security Command Center")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("DEMO DATA");
    expect(await screen.findAllByText("■ CONFIRMED")).not.toHaveLength(0);
  });
});

describe("App reload", () => {
  it("restores a demo session after a page reload", async () => {
    sessionStorage.clear();
    sessionStorage.setItem("bbj.mode", "demo");
    mount();
    expect(await screen.findByText("AI Agent Security Command Center")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("DEMO DATA");
  });
});
