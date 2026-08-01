import fs from 'node:fs';
import path from 'node:path';

function isRefString(value, graphLength) {
  return typeof value === 'string' && /^\d+$/.test(value) && Number(value) >= 0 && Number(value) < graphLength;
}

function decodeGraph(graph) {
  const memo = new Map();
  const inProgress = new Set();

  function resolveNode(node) {
    if (Array.isArray(node)) {
      return node.map(resolveValue);
    }
    if (node && typeof node === 'object') {
      const out = {};
      for (const [key, value] of Object.entries(node)) out[key] = resolveValue(value);
      return out;
    }
    return node;
  }

  function resolveIndex(index) {
    if (memo.has(index)) return memo.get(index);
    if (inProgress.has(index)) return `[Circular:${index}]`;
    inProgress.add(index);
    const resolved = resolveNode(graph[index]);
    memo.set(index, resolved);
    inProgress.delete(index);
    return resolved;
  }

  function resolveValue(value) {
    if (isRefString(value, graph.length)) return resolveIndex(Number(value));
    if (Array.isArray(value)) return value.map(resolveValue);
    if (value && typeof value === 'object') {
      const out = {};
      for (const [key, nested] of Object.entries(value)) out[key] = resolveValue(nested);
      return out;
    }
    return value;
  }

  return resolveIndex(0);
}

function getLatestJson(runData, nodeName) {
  const runs = runData?.[nodeName];
  if (!Array.isArray(runs) || runs.length === 0) return null;
  const lastRun = runs[runs.length - 1];
  const main = lastRun?.data?.main;
  if (!Array.isArray(main) || !Array.isArray(main[0]) || main[0].length === 0) return null;
  return main[0][main[0].length - 1]?.json ?? null;
}

function summarizeExecution(decoded) {
  const runData = decoded?.resultData?.runData ?? {};
  const normalized = getLatestJson(runData, 'Code: Normalize Payload');
  const merged = getLatestJson(runData, 'Code: Merge Envelope + Session');
  const validated = getLatestJson(runData, 'Code: Validate Dify Response');
  const queued = getLatestJson(runData, 'Postgres: Write Review Queue');

  return {
    normalize_payload: normalized,
    merge_envelope_session: merged,
    validate_dify_response: validated,
    write_review_queue: queued,
  };
}

function main() {
  const inputPath = process.argv[2];
  if (!inputPath) {
    console.error('Usage: node tools/decode_n8n_execution.mjs <execution-json-file>');
    process.exit(1);
  }

  const raw = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
  const encoded = raw?.data?.data;
  if (typeof encoded !== 'string') {
    console.error('File does not contain expected execution payload string');
    process.exit(2);
  }

  const graph = JSON.parse(encoded);
  const decoded = decodeGraph(graph);
  const summary = summarizeExecution(decoded);

  const outputPath = path.join(
    path.dirname(inputPath),
    path.basename(inputPath, path.extname(inputPath)) + '.decoded-summary.json',
  );
  fs.writeFileSync(outputPath, JSON.stringify(summary, null, 2), 'utf8');
  console.log(outputPath);
}

main();
