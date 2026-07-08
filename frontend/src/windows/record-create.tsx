/**
 * The create window (REQ-037): the `/records/:entityType/new` route the New
 * action opens. Loads the create-form view-model; the first save (or a
 * restore-instead / use-existing choice) lands on that record's read view in
 * this same window; Cancel before the first save creates nothing and
 * returns to the origin (back when there is history, otherwise the popup
 * closes).
 */

import { type ReactElement } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { type CreateFormPayload } from "../api/payloads";
import { useEnvelope } from "../api/useEnvelope";
import { CreateFormScreen } from "../forms/create-form";
import { NotificationBell } from "../shell/bell";
import { DeclinedNotice, EducateNotice, UnreachableNotice } from "../shell/educate";

export function RecordCreateWindow(): ReactElement {
  const { entityType } = useParams();
  const navigate = useNavigate();
  if (entityType === undefined) {
    // Unreachable under the declared route; typed as optional by the router.
    return (
      <EducateNotice
        notice={{
          whatHappened: "This window has nothing to create.",
          why: "The address does not name a data set.",
          whatNext: "Use a grid's New action to create a record.",
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
      <RecordCreateLoader
        entityType={entityType}
        onCreated={(recordId) => {
          navigate(`/records/${entityType}/${recordId}`);
        }}
        onLeave={() => {
          if (window.history.length > 1) {
            navigate(-1);
          } else {
            window.close();
          }
        }}
      />
    </div>
  );
}

function RecordCreateLoader({
  entityType,
  onCreated,
  onLeave,
}: {
  entityType: string;
  onCreated: (recordId: string) => void;
  onLeave: () => void;
}): ReactElement {
  const { state } = useEnvelope<CreateFormPayload>(
    `/records/${entityType}/create-form`,
  );
  switch (state.phase) {
    case "loading":
      return <p>Loading form…</p>;
    case "declined":
      return <DeclinedNotice errors={state.errors} />;
    case "unreachable":
      return <UnreachableNotice />;
    case "loaded":
      return (
        <CreateFormScreen
          entityType={entityType}
          payload={state.data}
          onCreated={onCreated}
          onLeave={onLeave}
        />
      );
  }
}
