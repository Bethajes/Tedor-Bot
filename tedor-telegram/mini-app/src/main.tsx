import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { readyTelegram } from "./api/telegram";
import "./index.css";

// Tell Telegram the webview is ready before the first paint settles, so the
// window sizes itself to the content rather than bouncing once React mounts.
readyTelegram();

const container = document.getElementById("root");
if (!container) {
  throw new Error("#root is missing from index.html");
}

ReactDOM.createRoot(container).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);