// Apply the global style tokens, then mount the SPA routes.
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import DashboardPage from "./pages/DashboardPage";
import LoginPage from "./pages/LoginPage";
import OauthCallbackPage from "./pages/OauthCallbackPage";
import GuildPage from "./pages/GuildPage";
import UsersPage from "./pages/UsersPage";
import { NavTracker } from "./lib/useEscapeBack";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <NavTracker />
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/auth/callback" element={<OauthCallbackPage />} />
        <Route path="/guilds/:guildId" element={<GuildPage />} />
        <Route path="/users" element={<UsersPage />} />
        <Route path="*" element={<LoginPage />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
);
