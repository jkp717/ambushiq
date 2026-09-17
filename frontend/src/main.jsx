import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./assets/styles.css";

// Registering a service worker (even one that does nothing but install/activate)
// is what makes Chrome/Android consider this PWA installable and fire the
// `beforeinstallprompt` event the Settings page's "Install app" button relies on.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

createRoot(document.getElementById("root")).render(<App />);
