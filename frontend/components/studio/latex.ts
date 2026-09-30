import type { Monaco } from "@monaco-editor/react";

let registered = false;

/** Minimal LaTeX highlighting; region markers stand out so it's clear what's editable. */
export function registerLatex(monaco: Monaco) {
  if (registered) return;
  registered = true;
  monaco.languages.register({ id: "latex" });
  monaco.languages.setMonarchTokensProvider("latex", {
    tokenizer: {
      root: [
        [/^\s*%%(BEGIN|END):[A-Z0-9_]+%%\s*$/, "region-marker"],
        [/%%(prio:[0-9.]+|pin)/, "annotation"],
        [/%.*$/, "comment"],
        [/\\[a-zA-Z@]+\*?/, "keyword"],
        [/\\./, "keyword"],
        [/[{}]/, "delimiter.bracket"],
        [/\$[^$]*\$/, "string"],
      ],
    },
  });
  for (const [name, base] of [["latex-light", "vs"], ["latex-dark", "vs-dark"]] as const) {
    monaco.editor.defineTheme(name, {
      base,
      inherit: true,
      rules: [
        { token: "region-marker", foreground: "B45309", fontStyle: "bold" },
        { token: "annotation", foreground: "059669", fontStyle: "italic" },
      ],
      colors: {},
    });
  }
}
