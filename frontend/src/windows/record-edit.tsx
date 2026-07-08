/**
 * The full-screen edit window (REQ-032): the `/records/:entityType/:recordId/edit`
 * route the Edit action opens. Loads the one edit-form view-model and hands
 * it to `EditFormScreen`; leaving (guard permitting) lands back on the
 * record's read view — the same window, so a pop-out's Edit stays in its
 * pinned window and the main window returns to where the grid sent it.
 */

import { type ReactElement } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { type EditFormPayload } from "../api/payloads";
import { useEnvelope } from "../api/useEnvelope";
import { EditFormScreen } from "../forms/edit-form";
import { NotificationBell } from "../shell/bell";
import { DeclinedNotice, EducateNotice, UnreachableNotice } from "../shell/educate";

export function RecordEditWindow(): ReactElement {
  const { entityType, recordId } = useParams();
  const navigate = useNavigate();
  if (entityType === undefined || recordId === undefined) {
    // Unreachable under the declared route; typed as optional by the router.
    return (
      <EducateNotice
        notice={{
          whatHappened: "This edit window has nothing to edit.",
          why: "The address does not name a record.",
          whatNext: "Open a record and use its Edit action.",
        }}
      />
    );
  }
  return (
    <div className="record-edit-window">
      <header>
        <span>CBM Mentoring</span>
        <NotificationBell />
      </header>
      <RecordEditLoader
        entityType={entityType}
        recordId={recordId}
        onLeave={() => {
          navigate(`/records/${entityType}/${recordId}`);
        }}
      />
    </div>
  );
}

function RecordEditLoader({
  entityType,
  recordId,
  onLeave,
}: {
  entityType: string;
  recordId: string;
  onLeave: () => void;
}): ReactElement {
  const { state } = useEnvelope<EditFormPayload>(
    `/records/${entityType}/${recordId}/edit-form`,
  );
  switch (state.phase) {
    case "loading":
      return <p>Loading edit form…</p>;
    case "declined":
      return <DeclinedNotice errors={state.errors} />;
    case "unreachable":
      return <UnreachableNotice />;
    case "loaded":
      return (
        <EditFormScreen
          entityType={entityType}
          recordId={recordId}
          payload={state.data}
          onLeave={onLeave}
        />
      );
  }
}
