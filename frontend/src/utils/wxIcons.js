import { Sun, CloudSun, Cloud, CloudDrizzle, CloudRain, CloudSnow, Moon, CloudMoon } from "lucide-react";

// Weather icon names from the backend (forecast.service._sky_icon) -> lucide icons: the day strip's day
// icons and the shelf's hourly cards ("night" / "night-partly" are hours between sunset and sunrise).
export const WX_ICONS = {
  sun: Sun, partly: CloudSun, cloud: Cloud, drizzle: CloudDrizzle, rain: CloudRain, snow: CloudSnow,
  night: Moon, "night-partly": CloudMoon,
};

// The same icons in words (the backend's _WX_TEXT, plus the night ones), for the shelf header's hour line.
export const WX_TEXT = {
  sun: "Sunny", partly: "Partly cloudy", cloud: "Cloudy", drizzle: "Drizzle", rain: "Rain", snow: "Snow",
  night: "Clear", "night-partly": "Partly cloudy",
};
