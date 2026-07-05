/**
 * Shell composition root (WTK-198, replacing the WTK-194 boot screen): the
 * main window hosts the urgent banner, a thin header with the notification
 * bell, and the Home panel; `/records/:entityType/:recordId` is the pop-out
 * record window. Every surface renders server view-models verbatim — the
 * Python modules in src/mentorapp/ui/ stay the single source of behavior.
 * Panel navigation, quick-open, and the full standard header are the
 * shell-rendering slice of PI-011; until it lands, every non-record path is
 * Home — never a blank screen.
 */

import { type ReactElement, useCallback, useState } from "react";
import { Route, Routes } from "react-router-dom";

import { HomePanel } from "./panels/home";
import { UrgentBanner } from "./shell/banner";
import { NotificationBell } from "./shell/bell";
import { RecordWindow } from "./windows/record";

function MainWindow(): ReactElement {
  // Home's render reads its messages (REQ-011 auto-read on view); bumping
  // this token re-fetches the banner so it never banners what was just read.
  const [messagesViewedAt, setMessagesViewedAt] = useState(0);
  const onMessagesViewed = useCallback(() => {
    setMessagesViewedAt((current) => current + 1);
  }, []);

  return (
    <div>
      <UrgentBanner refreshToken={messagesViewedAt} />
      <header>
        <span>CBM Mentoring</span>
        <NotificationBell />
      </header>
      <HomePanel onMessagesViewed={onMessagesViewed} />
    </div>
  );
}

export function App(): ReactElement {
  return (
    <Routes>
      <Route path="/records/:entityType/:recordId" element={<RecordWindow />} />
      <Route path="*" element={<MainWindow />} />
    </Routes>
  );
}
