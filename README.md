# OpenClaw Conversation for Home Assistant

Custom component that integrates [OpenClaw](https://openclaw.ai) as a conversation agent in Home Assistant's voice pipeline. Enables streaming voice responses through your OpenClaw gateway with full memory, skills, and context support.

## How it works

```
Wake word ("OK Nabu") → Satellite sends audio → HA Cloud STT → Text
    → OpenClaw (processes with memory/skills/context)
    → Streaming text response → HA Cloud TTS → Audio chunks → Satellite
```

1. Your voice satellite detects the wake word and streams audio to Home Assistant
2. HA Cloud STT transcribes the audio to text
3. The text is sent to your OpenClaw gateway via the OpenAI-compatible `/v1/chat/completions` endpoint
4. OpenClaw processes the request with its full agent capabilities (memory, skills, tools, context)
5. The response streams back as SSE chunks — each chunk is forwarded to TTS immediately
6. HA Cloud TTS converts text chunks to audio progressively
7. Audio is sent to the satellite as it's generated (no waiting for the full response)

If OpenClaw needs to control Home Assistant (lights, switches, etc.), it does so directly via the HA REST API through its Home Assistant skill — not through the voice pipeline.

## Prerequisites

### OpenClaw gateway

Your OpenClaw gateway must have the Chat Completions HTTP endpoint enabled. Add this to your `~/.openclaw/openclaw.json`:

```json5
{
  gateway: {
    http: {
      endpoints: {
        chatCompletions: { enabled: true }
      }
    }
  }
}
```

Restart the gateway after changing the config:

```bash
openclaw gateway restart
```

Verify it's running:

```bash
curl http://<gateway-host>:18789/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"openclaw:main","messages":[{"role":"user","content":"ping"}]}'
```

### Home Assistant

- Home Assistant 2024.12.0 or newer
- A voice satellite configured (ESP32-S3, etc.) or the HA app with voice
- HA Cloud (Nabu Casa) for STT/TTS, or another STT/TTS provider

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu (top right) → **Custom repositories**
3. Add this repository URL and select **Integration** as category
4. Search for "OpenClaw Conversation" and install it
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/openclaw_conversation/` folder into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

### Step 1: Add the integration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **OpenClaw Conversation**
3. Fill in the connection details:

| Field | Description | Example |
|-------|-------------|---------|
| **Gateway URL** | Full URL of your OpenClaw gateway | `http://192.168.1.50:18789` |
| **API Token** | Bearer token (leave empty if no auth) | |
| **Agent ID** | OpenClaw agent to use | `main` |

The setup will verify connectivity by sending a test request to the gateway.

### Step 2: Configure the voice pipeline

1. Go to **Settings → Voice Assistants**
2. Edit your voice pipeline (or create a new one):
   - **Speech-to-Text**: Home Assistant Cloud (or your preferred STT)
   - **Conversation Agent**: **OpenClaw Conversation**
   - **Text-to-Speech**: Home Assistant Cloud (or your preferred TTS)
3. Assign this pipeline to your voice satellite

### Options

After setup, you can configure additional options by clicking **Configure** on the integration:

- **System Prompt**: Optional prompt prepended to every conversation. Leave empty to use OpenClaw's default system prompt and personality.
- **Agent ID**: Change the OpenClaw agent used for conversations.

## Architecture

```
┌─────────────┐     audio     ┌──────────────────┐
│   Satellite  │──────────────►│  Home Assistant   │
│  (ESP32-S3)  │◄──────────────│                  │
└─────────────┘   audio chunks │  ┌────────────┐  │
                               │  │  HA Cloud   │  │
                               │  │  STT / TTS  │  │
                               │  └─────┬──────┘  │
                               │        │ text    │
                               │  ┌─────▼──────┐  │
                               │  │  OpenClaw   │  │
                               │  │ Conversation│  │
                               │  │  (this)     │  │
                               │  └─────┬──────┘  │
                               └────────┼─────────┘
                                        │ SSE stream
                               ┌────────▼─────────┐
                               │  OpenClaw Gateway │
                               │  (your server)    │
                               │                   │
                               │  memory / skills  │
                               │  context / tools  │
                               └───────────────────┘
```

## Session management

The integration passes the Home Assistant `conversation_id` as both the `user` field and `x-openclaw-session-key` header. This allows OpenClaw to:

- Maintain conversation context across multiple voice turns
- Use its session binding to associate the conversation with the correct agent session
- Preserve memory and context between interactions

## Streaming

This integration uses Home Assistant's `async_add_delta_content_stream` API to forward text chunks from OpenClaw's SSE stream directly into the voice pipeline. This means:

- TTS starts generating audio as soon as the first text chunk arrives
- The satellite starts playing audio before the full response is complete
- Perceived latency is significantly reduced compared to waiting for the full response

## Troubleshooting

### "Failed to connect to the OpenClaw gateway"

- Verify the gateway is running: `openclaw gateway status`
- Check the Chat Completions endpoint is enabled in your config
- Ensure Home Assistant can reach the gateway host/port (check firewall rules)
- Test with curl from the HA host

### "Authentication failed"

- If your gateway uses token auth, make sure you entered the correct token
- Check `gateway.auth.mode` and `gateway.auth.token` in your OpenClaw config

### Slow responses

- This is usually the LLM inference time on the OpenClaw side, not the integration
- The streaming architecture means you'll hear the beginning of the response while the rest is still being generated
- Check `openclaw logs` for any issues on the gateway side

### No audio output

- Verify TTS is configured in your voice pipeline
- Check that the satellite is connected and assigned to the correct pipeline
- Look at HA logs for errors from the `openclaw_conversation` integration

## License

MIT
