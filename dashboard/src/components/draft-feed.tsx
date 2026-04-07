"use client";

import { useState } from "react";
import { Draft } from "@/lib/db";
import { DraftCard } from "./draft-card";

export function DraftFeed({ initialDrafts }: { initialDrafts: Draft[] }) {
  const [drafts, setDrafts] = useState(initialDrafts);

  const handleUpdate = (updated: Draft) => {
    setDrafts((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
  };

  const handleDelete = (id: number) => {
    setDrafts((prev) => prev.filter((d) => d.id !== id));
  };

  return (
    <div className="space-y-4">
      {drafts.map((draft) => (
        <DraftCard
          key={draft.id}
          draft={draft}
          onUpdate={handleUpdate}
          onDelete={handleDelete}
        />
      ))}
    </div>
  );
}
