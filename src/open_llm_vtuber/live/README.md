# Live integration slice

This package is the in-process foundation for Bilibili/live-streaming support.
It is deliberately not a second VTuber process.

## What is implemented

- `RoomEvent`: validated canonical event contract for chat, gifts and operator
  messages. Viewer text is rendered as escaped JSON when it reaches the model.
- `LiveDirector`: bounded queue, dedupe by `platform + room + event_id`, event
  age filtering, same-priority batching, gift/chat priority separation, and a
  single-consumer callback.
- `CharacterProfile` / `EmotionState`: versioned stable persona plus bounded
  mood/energy state; the stable profile is never replaced by retrieved memory.
- `MemoryManager` / `StreamContextBuilder`: scope-isolated facts with provenance
  and one budgeted context packet per turn.
- `RundownState`: linear programme state with pause/resume, current-segment
  context and bounded history.
- `LiveSession`: starts a director and forwards each selected `LiveBatch` to an
  OLV agent, then forwards OLV outputs to the caller's output handler. It also
  owns the optional proactive scheduler, which reuses the same agent; it is not
  a second decision loop.
- `ProactiveScheduler`: emits at most one synthetic `proactive` batch per
  interval, only while a rundown is running and the director/agent is idle.
- `LiveStreamerAgent`: an `AgentInterface` implementation that separates
  `LiveReActPlanner` (speak/silent decision) from `LiveReplyer` (one short
  spoken line), then delegates sentence splitting, TTS filtering and Live2D
  action extraction to OLV's existing pipeline.
- `LiveReActPlanner`: bounded JSON decision loop; supports one bounded session
  recap recall step, not arbitrary tool execution.
- `SessionRecapWriter`: writes a factual recap after a session ends, with a
  deterministic fallback when the LLM is unavailable.
- `map_bilibili_payload`: normalized Bilibili payload adapter.
- `BilibiliOfficialCollector`: Open Platform WebSocket adapter requiring the
  platform's `id_code`, `app_id`, `access_key`, and `access_key_secret`. It emits
  normalized `RoomEvent` objects into `LiveDirector`; credentials are never
  committed and live tests require a real authorized room.

## Wiring boundary

The OLV conversation layer remains the only owner of TTS playback and Live2D.
The intended call path is:

```text
Bilibili collector -> RoomEvent -> LiveDirector -> LiveBatch
  -> LiveStreamerAgent.chat(make_live_batch_input(batch))
  -> OLV sentence/TTS pipeline -> output handler -> frontend playback
```

`/live-ws` is registered by `server.py` and requires
`conversation_agent_choice: live_streamer_agent`. It accepts normalized
`RoomEvent` (`{"type": "room-event", "event": {...}}`), a normalized Bilibili
payload (`{"type": "bilibili-event", "payload": {...}}`), or starts the official
collector with `{"type": "bilibili-start", "config": {...}}`. Outputs use the
same OLV browser TTS/Live2D path.

The `bilibili-start` route is local-only and should be protected by the same
network boundary as `/client-ws`; credentials are not persisted by this route.
The official collector still requires an authorized Bilibili Open Platform app
and a real room for acceptance.

`/live-ws` also accepts:

```json
{"type":"rundown-load","segments":[{"id":"open","title":"开场","goal":"问候","expected_ms":60000}],"auto_begin":true}
```

```json
{"type":"proactive","enabled":true,"interval_ms":30000}
```

```json
{"type":"rundown-control","action":"next","by":"operator"}
```

```json
{"type":"notebook","action":"set","content":"本场话题：开场、游戏、问答"}
```

Proactive speech is disabled by default and uses the same LiveStreamerAgent,
TTS and Live2D pipeline. It is not a second process or a second audio owner.
The live status also exposes `emotion`, `memories` and `context_trace` for
debugging the current state and context budget.

## Choosing the agent

Set `character_config.agent_config.conversation_agent_choice` to
`live_streamer_agent` and configure `agent_settings.live_streamer_agent`. The
default remains `basic_memory_agent`.

## Recent fixes (2026-10-31)

- The normal conversation path now waits for the ordered TTS sender
  (`TTSTaskManager.wait_idle`) before sending exactly one
  `backend-synth-complete`; `single_conversation`/`group_conversation` no longer
  send a duplicate.
- `LiveDirector` selects the highest-priority band first (gift/operator before a
  chat backlog) and evicts the oldest low-priority item when the queue is full.
- `/live-ws` cleans up `live_active`, the store session and TTS tasks when a
  session fails to start or disconnects; `start_bilibili` awaits a real async
  collector callback.
- `live_chat` records both viewer-side batch text and assistant lines, so
  session recaps can reconstruct the dialogue.
- Event text/names/IDs strip C0/C1, bidi and zero-width characters.
- Review record: `doc/live-code-review-2026-10-31.md`.

## Tests

```text
python -m unittest discover -s tests -p "test_*.py"
```
