import { useState, useEffect, useCallback } from "react";

function isStandaloneNow() {
  try {
    return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  } catch { return false; }
}

function isIOSDevice() {
  const ua = navigator.userAgent || "";
  const iOSUA = /iphone|ipad|ipod/i.test(ua);
  // iPadOS 13+ reports as a Mac in desktop mode — the touch-point check is
  // the standard workaround to still detect it as an iPad.
  const iPadDesktopMode = navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1;
  return iOSUA || iPadDesktopMode;
}

// Wraps the browser's native "add to home screen" flow: on Chrome/Android
// (and desktop Chrome/Edge) this lets us show our own "Install app" button
// instead of waiting for the browser's own icon. iOS Safari has no such API
// at all — isIOS is exposed so the caller can show manual instructions instead.
function useInstallPrompt() {
  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [installed, setInstalled] = useState(isStandaloneNow);

  useEffect(() => {
    function onBeforeInstallPrompt(e) {
      e.preventDefault();
      setDeferredPrompt(e);
    }
    function onAppInstalled() {
      setDeferredPrompt(null);
      setInstalled(true);
    }
    window.addEventListener("beforeinstallprompt", onBeforeInstallPrompt);
    window.addEventListener("appinstalled", onAppInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onBeforeInstallPrompt);
      window.removeEventListener("appinstalled", onAppInstalled);
    };
  }, []);

  const promptInstall = useCallback(async () => {
    if (!deferredPrompt) return false;
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    setDeferredPrompt(null);
    return outcome === "accepted";
  }, [deferredPrompt]);

  return { canInstall: !!deferredPrompt, promptInstall, installed, isIOS: isIOSDevice() };
}

export { useInstallPrompt };
