# Live integration slice

This package is the in-process foundation for Bilibili/live-streaming support.
It is deliberately not a second VTuber process.

## What is implemented

- `RoomEvent`: validated canonical event contract for chat, gifts and operator
  messages. Viewer text is rendered as escaped JSON when it reaches the model.
- `LiveDirector`: bounded queue, dedupe by `platform + room + event_id`, event
  age filtering, same-priority batching, gift/chat priority separation, and a
  single-consumer callback.
- `RundownState`: linear programme state with pause/resume, current-segment
  context and bounded history.
- `LiveSession`: starts a director and forwards each selected `LiveBatch` to an
  OLV agent, then forwards OLV outputs to the caller's output handler.
- `LiveStreamerAgent`: an `AgentInterface` implementation that applies a planner
  decision and delegates sentence splitting, TTS filtering and Live2D action
  extraction to the existing OLV `BasicMemoryAgent` pipeline.
- `map_bilibili_payload`: normalized Bilibili payload adapter. Network collection
  remains an injected port because authorization and collector choice are
  deployment concerns.

## Wiring boundary

The OLV conversation layer remains the only owner of TTS playback and Live2D.
The intended call path is:

```text
Bilibili collector -> RoomEvent -> LiveDirector -> LiveBatch
  -> LiveStreamerAgent.chat(make_live_batch_input(batch))
  -> OLV sentence/TTS pipeline -> output handler -> frontend playback
```

`/live-ws` is registered by `server.py` and requires
`conversation_agent_choice: live_streamer_agent`. It accepts either a normalized
`RoomEvent` (`{"type": "room-event", "event": {...}}`) or a Bilibili payload
(`{"type": "bilibili-event", "payload": {...}}`), and sends OLV audio/actions
through `LiveBrowserOutput`. It does not start a Bilibili network collector;
authorization and collector deployment remain explicit operational inputs.

## Choosing the agent

Set `character_config.agent_config.conversation_agent_choice` to
`live_streamer_agent` and configure `agent_settings.live_streamer_agent`. The
default remains `basic_memory_agent`.

## Tests

```text
python -m unittest discover -s tests -v
```
