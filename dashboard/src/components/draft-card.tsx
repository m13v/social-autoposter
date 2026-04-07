"use client";

import { useState } from "react";
import { Draft } from "@/lib/db";

const PLATFORM_COLORS: Record<string, string> = {
  reddit: "bg-orange-100 text-orange-800",
  twitter: "bg-sky-100 text-sky-800",
  linkedin: "bg-blue-100 text-blue-800",
  moltbook: "bg-purple-100 text-purple-800",
  email: "bg-green-100 text-green-800",
};

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-yellow-100 text-yellow-800",
  approved: "bg-green-100 text-green-800",
  sent: "bg-gray-100 text-gray-600",
  rejected: "bg-red-100 text-red-800",
  edited: "bg-indigo-100 text-indigo-800",
};

export function DraftCard({
  draft,
  onUpdate,
  onDelete,
}: {
  draft: Draft;
  onUpdate: (d: Draft) => void;
  onDelete: (id: number) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [editBody, setEditBody] = useState(draft.edited_body || draft.body);
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(false);

  async function act(action: string, extra?: Record<string, string>) {
    setLoading(true);
    try {
      const res = await fetch(`/api/drafts/${draft.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, ...extra }),
      });
      const updated = await res.json();
      onUpdate(updated);
      setEditing(false);
      setNote("");
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete() {
    if (!confirm("Delete this draft?")) return;
    setLoading(true);
    await fetch(`/api/drafts/${draft.id}`, { method: "DELETE" });
    onDelete(draft.id);
  }

  const timeAgo = formatTimeAgo(draft.created_at);

  return (
    <div className="border border-gray-200 rounded-lg p-5 bg-white shadow-sm">
      {/* Header */}
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <span
          className={`px-2 py-0.5 rounded text-xs font-semibold uppercase ${
            PLATFORM_COLORS[draft.platform] || "bg-gray-100 text-gray-800"
          }`}
        >
          {draft.platform}
        </span>
        <span
          className={`px-2 py-0.5 rounded text-xs font-semibold ${
            STATUS_COLORS[draft.status] || "bg-gray-100"
          }`}
        >
          {draft.status}
        </span>
        <span className="text-xs text-gray-400">{draft.content_type}</span>
        {draft.project_name && (
          <span className="text-xs text-gray-500 bg-gray-50 px-2 py-0.5 rounded">
            {draft.project_name}
          </span>
        )}
        <span className="text-xs text-gray-400 ml-auto">{timeAgo}</span>
      </div>

      {/* Target context */}
      {draft.target_title && (
        <div className="mb-3 p-3 bg-gray-50 rounded text-sm">
          <div className="font-medium text-gray-700 mb-1">
            Replying to: {draft.target_title}
          </div>
          {draft.target_author && (
            <div className="text-xs text-gray-500 mb-1">
              by {draft.target_author}
            </div>
          )}
          {draft.target_snippet && (
            <div className="text-gray-600 text-xs line-clamp-3">
              {draft.target_snippet}
            </div>
          )}
          {draft.target_url && (
            <a
              href={draft.target_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-blue-600 hover:underline mt-1 inline-block"
            >
              View original
            </a>
          )}
        </div>
      )}

      {/* Draft title */}
      {draft.title && (
        <h3 className="font-semibold text-gray-900 mb-2">{draft.title}</h3>
      )}

      {/* Draft body */}
      {editing ? (
        <textarea
          className="w-full border border-gray-300 rounded p-3 text-sm font-mono resize-y min-h-[120px] focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={editBody}
          onChange={(e) => setEditBody(e.target.value)}
          rows={6}
        />
      ) : (
        <div className="text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">
          {draft.edited_body || draft.body}
        </div>
      )}

      {/* Client note (if edited/rejected) */}
      {draft.client_note && !editing && (
        <div className="mt-3 p-2 bg-yellow-50 border border-yellow-200 rounded text-sm text-yellow-800">
          Note: {draft.client_note}
        </div>
      )}

      {/* Note input for editing/rejecting */}
      {editing && (
        <input
          type="text"
          placeholder="Add a note (optional)"
          className="w-full mt-2 border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      )}

      {/* Actions */}
      {draft.status === "pending" || draft.status === "edited" ? (
        <div className="flex gap-2 mt-4 flex-wrap">
          {!editing ? (
            <>
              <button
                onClick={() => act("approve")}
                disabled={loading}
                className="px-4 py-2 bg-green-600 text-white rounded text-sm font-medium hover:bg-green-700 disabled:opacity-50 transition-colors"
              >
                Approve & Send
              </button>
              <button
                onClick={() => setEditing(true)}
                disabled={loading}
                className="px-4 py-2 bg-white border border-gray-300 text-gray-700 rounded text-sm font-medium hover:bg-gray-50 disabled:opacity-50 transition-colors"
              >
                Edit
              </button>
              <button
                onClick={() => {
                  const reason = prompt("Reason for rejection (optional):");
                  if (reason !== null)
                    act("reject", { client_note: reason });
                }}
                disabled={loading}
                className="px-4 py-2 bg-white border border-red-300 text-red-600 rounded text-sm font-medium hover:bg-red-50 disabled:opacity-50 transition-colors"
              >
                Reject
              </button>
              <button
                onClick={handleDelete}
                disabled={loading}
                className="px-4 py-2 text-gray-400 text-sm hover:text-red-500 disabled:opacity-50 transition-colors ml-auto"
              >
                Delete
              </button>
            </>
          ) : (
            <>
              <button
                onClick={() =>
                  act("edit", { edited_body: editBody, client_note: note })
                }
                disabled={loading}
                className="px-4 py-2 bg-indigo-600 text-white rounded text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors"
              >
                Save Edit
              </button>
              <button
                onClick={() => {
                  setEditing(false);
                  setEditBody(draft.edited_body || draft.body);
                  setNote("");
                }}
                className="px-4 py-2 bg-white border border-gray-300 text-gray-700 rounded text-sm font-medium hover:bg-gray-50 transition-colors"
              >
                Cancel
              </button>
            </>
          )}
        </div>
      ) : draft.status === "approved" ? (
        <div className="flex gap-2 mt-4">
          <button
            onClick={() => act("send")}
            disabled={loading}
            className="px-4 py-2 bg-blue-600 text-white rounded text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            Mark as Sent
          </button>
        </div>
      ) : null}

      {/* Account info */}
      {draft.our_account && (
        <div className="mt-3 text-xs text-gray-400">
          Posting as: {draft.our_account}
        </div>
      )}
    </div>
  );
}

function formatTimeAgo(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay}d ago`;
}
