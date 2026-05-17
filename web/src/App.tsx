import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { Overview } from "./routes/Overview";
import { Meetings } from "./routes/Meetings";
import { Meeting } from "./routes/Meeting";
import { Briefings } from "./routes/Briefings";
import { Briefing } from "./routes/Briefing";
import { Add } from "./routes/Add";
import { Editor } from "./routes/Editor";
import { Prompts } from "./routes/Prompts";
import { Stub } from "./routes/Stub";
import { Login } from "./routes/Login";
import { Settings } from "./routes/Settings";
import { Admin } from "./routes/Admin";

export function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/overview" replace />} />
          <Route path="/overview" element={<Overview />} />
          <Route path="/meetings" element={<Meetings />} />
          <Route path="/meeting/:id" element={<Meeting />} />
          <Route path="/briefings" element={<Briefings />} />
          <Route path="/briefing/:id" element={<Briefing />} />
          <Route path="/add" element={<Add />} />
          <Route path="/edit/:type/:id" element={<Editor />} />
          <Route path="/deepdive" element={<Stub name="deepdive" />} />
          <Route path="/bulk" element={<Stub name="bulk" />} />
          <Route path="/prompts" element={<Prompts />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}
