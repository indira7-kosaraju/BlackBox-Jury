import "@fontsource-variable/inter";
import "@fontsource/space-grotesk/500.css";
import "@fontsource/space-grotesk/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/600.css";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/shell.css";
import "./styles/pages.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { SessionProvider } from "./app/session";
import { ToastProvider } from "./components/ui";

const root = document.getElementById("root");
if (!root) throw new Error("#root missing");

createRoot(root).render(
  <StrictMode>
    <BrowserRouter basename="/ui">
      <SessionProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </SessionProvider>
    </BrowserRouter>
  </StrictMode>,
);
