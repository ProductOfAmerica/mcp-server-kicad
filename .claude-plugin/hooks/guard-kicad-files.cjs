#!/usr/bin/env node
/**
 * KiCad File Guard Hook (ships with kicad plugin)
 *
 * PreToolUse handler — blocks Read, Write, and Edit tool calls
 * on KiCad file extensions. All KiCad file manipulation MUST go
 * through the kicad MCP server tools.
 */

const fs = require('fs');

const KICAD_EXTENSIONS = [
  '.kicad_sch',
  '.kicad_pcb',
  '.kicad_sym',
  '.kicad_mod',
  '.kicad_pro',
  '.kicad_prl',
];

function readInput() {
  try {
    const data = fs.readFileSync(0, 'utf-8');
    return JSON.parse(data);
  } catch {
    return {};
  }
}

function main() {
  const input = readInput();

  if (input.hook_event_name !== 'PreToolUse') return;

  const toolName = input.tool_name || '';
  if (toolName !== 'Read' && toolName !== 'Write' && toolName !== 'Edit') return;

  const toolInput = input.tool_input || {};
  const filePath = toolInput.file_path || toolInput.path || '';

  const isKicadFile = KICAD_EXTENSIONS.some(ext => filePath.endsWith(ext));
  if (!isKicadFile) return;

  console.log(JSON.stringify({
    decision: 'block',
    reason: `BLOCKED: Cannot use ${toolName} on KiCad file "${filePath}". All KiCad file manipulation MUST go through the kicad MCP server tools (e.g., create_schematic, add_symbol, place_symbol, add_wire, etc.). NEVER use Read/Write/Edit on KiCad files.`
  }));
}

main();
