import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./assets/styles.css";

// The service worker makes this PWA installable (Chrome/Android fire `beforeinstallprompt`,
// which the Settings page's "Install app" button relies on) and keeps the app's own files so
// it still opens with no signal (see public/sw.js).
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

createRoot(document.getElementById("root")).render(<App />);
