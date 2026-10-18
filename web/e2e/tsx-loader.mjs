import { readFileSync } from "node:fs";
import { isBuiltin } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const srcRoot = path.join(webRoot, "src");

function shouldTranspile(url) {
  return url.startsWith("file:") && (url.endsWith(".ts") || url.endsWith(".tsx"));
}

function fileIfExists(candidate) {
  try {
    readFileSync(candidate);
    return pathToFileURL(candidate).href;
  } catch {
    return null;
  }
}

function resolveSource(base) {
  const candidates = [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    path.join(base, "index.ts"),
    path.join(base, "index.tsx"),
  ];
  for (const candidate of candidates) {
    const url = fileIfExists(candidate);
    if (url) return url;
  }
  return null;
}

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith("@/")) {
    const url = resolveSource(path.join(srcRoot, specifier.slice(2)));
    if (url) return { url, shortCircuit: true };
  }
  if (
    (specifier.startsWith("./") || specifier.startsWith("../")) &&
    context.parentURL?.startsWith("file:")
  ) {
    const parentFile = fileURLToPath(context.parentURL);
    if (parentFile.startsWith(srcRoot) || parentFile.includes(`${path.sep}e2e${path.sep}`)) {
      const url = resolveSource(path.resolve(path.dirname(parentFile), specifier));
      if (url) return { url, shortCircuit: true };
    }
  }
  if (isBuiltin(specifier) || specifier.startsWith("node:")) {
    return nextResolve(specifier, context);
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (!shouldTranspile(url)) return nextLoad(url, context);
  const source = readFileSync(fileURLToPath(url), "utf8");
  const jsx = url.endsWith(".tsx");
  const out = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
      jsx: jsx ? ts.JsxEmit.ReactJSX : ts.JsxEmit.Preserve,
      esModuleInterop: true,
      verbatimModuleSyntax: false,
    },
    fileName: url,
  });
  return {
    format: "module",
    source: out.outputText,
    shortCircuit: true,
  };
}
