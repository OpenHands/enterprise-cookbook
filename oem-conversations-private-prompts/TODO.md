# OEM Conversations with Private Prompts - Testing & TODO

## ✅ Completed Testing

### Component Testing

- [x] **proprietary-plugin**: JSON structure validated, prompts load correctly
- [x] **mcp-server**: FastAPI server starts, health endpoint works
- [x] **MCP Protocol**: SSE transport working (fixed from streamable-http)
- [x] **Database**: SQLite schema works, project/request tracking functional

### Integration Testing

- [x] **Project seeding**: `/projects` endpoint creates project mappings
- [x] **Guide request flow**: `request_travel_guide` MCP tool creates requests
- [x] **Private conversation spawning**: OpenHands API starts private conversations
- [x] **Conversation ID extraction**: Fixed to use `app_conversation_id` from start task
- [x] **Shared sandbox**: Both conversations access same filesystem
- [x] **Web server**: Private conversation starts server on port 12000
- [x] **Callback architecture**: `/guide-complete` endpoint receives URL from private conversation
- [x] **End-to-end flow**: Customer requests → MCP server → private conv → callback → URL available

### Successful Test Runs

| Date | Destination | Status | URL |
|------|-------------|--------|-----|
| 2026-05-16 | Barcelona | ✅ Completed | https://work-1-vbtxgifhrfrxvtzk.prod-runtime.all-hands.dev/travel_guide.html |
| 2026-05-16 | Tokyo | ✅ Completed | Guide generated (before callback refactor) |
| 2026-05-16 | Rome | ✅ Completed | Guide generated (before callback refactor) |
| 2026-05-16 | Paris | ✅ Completed | Guide generated (before callback refactor) |

## 🔄 In Progress

- [ ] Verify `check_guide_status` returns URL correctly to customer conversation
- [ ] Test full customer conversation flow (request → wait → get URL → display)

## 📋 TODO

### Core Functionality

- [ ] **Demo script** (`entry-point/demo.py`): Create/update to run full demo
- [ ] **Error handling**: Add error callback for failed generations
- [ ] **Timeout handling**: Mark requests as failed if no callback within N minutes
- [ ] **Retry logic**: Handle transient failures in private conversation startup

### Security Testing

- [ ] **Prompt injection tests**: Verify customer can't extract proprietary prompts
- [ ] **Jailbreak attempts**: Test "ignore previous instructions" style attacks
- [ ] **Credential exposure**: Verify API keys never leak to customer conversation
- [ ] **File access**: Ensure customer can't read proprietary plugin files

### Documentation

- [x] **README.md**: Updated with callback architecture diagrams
- [ ] **mcp-server/README.md**: Document `/guide-complete` endpoint
- [ ] **Environment variables**: Document `MCP_SERVER_PUBLIC_URL` requirement

### Polish

- [ ] **Logging**: Improve log messages for debugging
- [ ] **Error messages**: User-friendly errors for common failures
- [ ] **Rate limiting**: Prevent abuse of guide generation
- [ ] **Cleanup**: Remove unused polling code from conversation_manager.py

## 🐛 Known Issues

1. **Sandbox URL discovery**: Private conversation must discover its own public URL via `$SANDBOX_RUNTIME_URL` environment variable - this works but should be documented clearly in the proprietary plugin.

2. **Guide generation time**: Takes 3-5 minutes typically. Customer conversation should provide engaging content while waiting.

## 📝 Architecture Notes

### Callback Flow (Current Implementation)

```
1. Customer calls request_travel_guide via MCP
2. MCP server creates DB record, starts private conversation
3. Private conversation receives callback URL + auth token in prompt
4. Private conversation generates guide, starts web server
5. Private conversation calls POST /guide-complete with URL
6. MCP server updates DB with completed status + URL
7. Customer calls check_guide_status, gets URL
```

### Environment Variables Required

| Variable | Description | Example |
|----------|-------------|---------|
| `OPENHANDS_API_KEY` | OpenHands Cloud API key | `oh_...` |
| `MCP_AUTH_TOKEN` | Token for MCP authentication | `wanderlust-mcp-secret-token` |
| `MCP_SERVER_PUBLIC_URL` | Public URL of MCP server (for callbacks) | `https://work-2-xxx.prod-runtime.all-hands.dev` |

## 🔗 Related Resources

- PR #1: https://github.com/OpenHands/enterprise-cookbook/pull/1
- Branch: `feature/oem-conversations-private-prompts`
- Original conversation: `f39bdade83f345cb96c3da11c948f838`
