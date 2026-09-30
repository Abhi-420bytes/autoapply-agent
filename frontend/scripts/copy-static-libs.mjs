// Serve Monaco and the PDF.js worker from this app instead of a CDN (no third-party
// scripts on a dashboard that handles credentials). Runs before dev/build.
import { cpSync, existsSync, mkdirSync } from "node:fs";

mkdirSync("public/monaco", { recursive: true });
if (!existsSync("public/monaco/vs")) cpSync("node_modules/monaco-editor/min/vs", "public/monaco/vs", { recursive: true });
cpSync("node_modules/pdfjs-dist/build/pdf.worker.min.mjs", "public/pdf.worker.min.mjs");
console.log("copied monaco + pdf.js worker into public/");
