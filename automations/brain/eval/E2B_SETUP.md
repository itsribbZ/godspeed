# E2B MCP Server Setup — Sandboxed Code Execution

## What it does
Firecracker microVM sandboxes for running LLM-generated code safely.
Sub-200ms cold starts, sessions up to 24hr (Pro). Free tier available.

## Setup (when ready)

1. Sign up: https://e2b.dev/
2. Get API key from dashboard
3. Install the MCP server:
   ```
   npx @anthropic-ai/mcp install e2b
   ```
   OR add to `~/.claude/.mcp.json`:
   ```json
   {
     "mcpServers": {
       "e2b": {
         "command": "npx",
         "args": ["-y", "@e2b/mcp-server"],
         "env": {
           "E2B_API_KEY": "your-key-here"
         }
       }
     }
   }
   ```
4. Restart Claude Code

## What it enables
- MUSES workers can execute generated code in isolated VMs
- Zeus can verify implementations by running them
- brain_audit.py could run test scripts in sandbox
- Safe execution of untrusted/generated Python/JS/C++

## Cost
- Free tier: generous for dev use
- Pro: longer sessions, higher throughput

## Status: BLOCKED on API key signup. Add to next session when the user activates.
