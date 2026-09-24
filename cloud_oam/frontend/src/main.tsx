import React from "react";
import ReactDOM from "react-dom/client";
import PublicKnowledge from "./PublicKnowledge";

// Update any root-scoped worker from the former warehouse homepage.
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><PublicKnowledge /></React.StrictMode>,
);
