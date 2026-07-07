/**
 * The rich-text entry seam (REQ-090, WTK-177). WHY a contenteditable seam:
 * the design-time component pick is CKEditor 5 (ui/entry_editors.py
 * RICH_TEXT_COMPONENT — REQ-090's adoption decision, licensing review
 * flagged for WTK-205; TipTap is the named fallback), and that dependency
 * adoption has not landed in this frontend yet. Until it does, this module
 * presents the SAME contract the design fixes — clean HTML value in/out and
 * the approved toolbar capability set (RICH_TEXT_CAPABILITIES: structure,
 * lists, links, undo/redo, clear formatting) — so swapping the component in
 * touches only this file, never the surfaces that entry fields render
 * through. Sanitization on save stays the server's shared-normalization
 * job (DB-S13), exactly as the design states.
 */

import { type ReactElement, useEffect, useRef } from "react";

/** The approved toolbar set (entry_editors.RICH_TEXT_CAPABILITIES), mapped
 * to document.execCommand verbs — the capability vocabulary is the
 * contract; the component adoption re-maps it onto its own toolbar. */
const TOOLBAR: readonly { title: string; glyph: string; command: string }[] = [
  { title: "Undo", glyph: "↶", command: "undo" },
  { title: "Redo", glyph: "↷", command: "redo" },
  { title: "Bold", glyph: "B", command: "bold" },
  { title: "Italic", glyph: "I", command: "italic" },
  { title: "Underline", glyph: "U", command: "underline" },
  { title: "Strikethrough", glyph: "S", command: "strikeThrough" },
  { title: "Bulleted list", glyph: "•≡", command: "insertUnorderedList" },
  { title: "Numbered list", glyph: "1≡", command: "insertOrderedList" },
  { title: "Outdent", glyph: "⇤", command: "outdent" },
  { title: "Indent", glyph: "⇥", command: "indent" },
  { title: "Insert link", glyph: "🔗", command: "createLink" },
  { title: "Clear formatting", glyph: "⌫F", command: "removeFormat" },
];

export interface RichTextEditorProps {
  label: string;
  /** The HTML to (re)load; applied when `resetToken` changes, not per keystroke. */
  initialHtml: string;
  /** Bump to reload `initialHtml` (e.g. switching to another session). */
  resetToken: string;
  onChange: (html: string) => void;
  /** REQ-089 fill weight — the editor's share of the panel's free height. */
  fillWeight: number;
}

export function RichTextEditor({
  label,
  initialHtml,
  resetToken,
  onChange,
  fillWeight,
}: RichTextEditorProps): ReactElement {
  const editorRef = useRef<HTMLDivElement>(null);

  // contenteditable owns its DOM between edits — React only (re)loads the
  // content when the subject changes, so typing is never clobbered by a
  // re-render.
  useEffect(() => {
    if (editorRef.current !== null) {
      editorRef.current.innerHTML = initialHtml;
    }
    // initialHtml is deliberately not a dependency: resetToken IS the
    // "load new content" signal; parent state echoing edits back must not
    // reset the caret.
  }, [resetToken]);

  const run = (command: string): void => {
    if (command === "createLink") {
      const url = window.prompt("Link URL:");
      if (url === null || url === "") {
        return;
      }
      // execCommand is deprecated but IS the contenteditable seam's tool;
      // it leaves with the REQ-090 component adoption (module docstring).
      // eslint-disable-next-line @typescript-eslint/no-deprecated
      document.execCommand(command, false, url);
    } else {
      // eslint-disable-next-line @typescript-eslint/no-deprecated -- same seam
      document.execCommand(command);
    }
    if (editorRef.current !== null) {
      onChange(editorRef.current.innerHTML);
    }
  };

  return (
    <div
      className="rich-text-entry"
      // REQ-089: entry areas auto-resize to fill their panel — the flex rule
      // is entry_editors.EntryEditor.flex_rule's exact shape (weights over a
      // zero basis, the 90px readability floor, scroll below the floor).
      style={{ flex: `${String(fillWeight)} 1 0`, minHeight: "90px" }}
    >
      <div className="editor-toolbar" role="toolbar" aria-label={`${label} formatting`}>
        {TOOLBAR.map((entry) => (
          <button
            key={entry.command}
            type="button"
            title={entry.title}
            // Keep focus (and the selection the command applies to) in the
            // editable region — a toolbar click must not steal the caret.
            onMouseDown={(event) => {
              event.preventDefault();
            }}
            onClick={() => {
              run(entry.command);
            }}
          >
            {entry.glyph}
          </button>
        ))}
      </div>
      <div
        ref={editorRef}
        className="notes-editor"
        contentEditable
        role="textbox"
        aria-multiline="true"
        aria-label={label}
        onInput={(event) => {
          onChange(event.currentTarget.innerHTML);
        }}
      />
    </div>
  );
}
