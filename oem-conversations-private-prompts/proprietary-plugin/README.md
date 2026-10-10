# Proprietary Plugin

Plugin for the private conversation (hidden from customers).

## Purpose

This plugin contains all the "secret sauce":
- Proprietary prompts for generating travel guides
- Detailed styling and branding instructions
- Access to secret data sources (Uncle Mortimer's network)
- Specialized techniques the customer should never see

## ⚠️ This Plugin is NEVER Loaded in Customer Conversations

The MCP server loads this plugin only in private conversations that run in the background. The customer-facing agent has no access to this plugin's prompts or capabilities.

## Files

- `SKILL.md` - The proprietary system prompts (the real secret sauce)
- `uncle_mortimers_secrets.json` - Network of eccentric restaurateurs with secret menu items

## Secret Contents

### System Prompt (SKILL.md)
Contains detailed instructions for:
- The "Wanderlust™ Brand Guide Format" HTML/CSS structure
- How to query Uncle Mortimer's Secret Menu Database
- The proprietary "Vibes-to-Venue Mapping Protocol"
- Specific tone and voice guidelines

### Data File
- `uncle_mortimers_secrets.json` - Restaurant database with:
  - Secret menu items and passwords
  - Backstories connecting to "Uncle Mortimer"
  - Vibe-to-venue mappings for personalization

## Output

The plugin generates:
- A complete HTML travel guide placed in the shared sandbox at `/workspace/travel_guide.html`
- Starts a Python HTTP server on port 12000 to serve the guide
- The external URL is available at the sandbox's work-1 host
