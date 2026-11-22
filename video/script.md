# ClawBoard - demo film script (60-second cut)

**Style.** Calm and technical, like a bench at night: near-black background, the board's own
colours (copper red, pad gold, signal green for fixes, alarm red for faults), monospace
numerals. The motion graphics use the dashboard's palette and font stacks, so the film and the
product look like one thing. Music is a slow minor pad with a sparse arpeggio, kept well under
the voice.

One cut only, within the 60-second limit for the X submission. Numbers in rows 4 and 6 are
filled from `assets/report.json` (the recorded live run) when `narrate.py` and `render_all.py`
run; they are never typed by hand.

| # | Scene | On screen | Narration |
|---|---|---|---|
| 1 | `scene: cold` | The real sample board, slow push-in, its DFM markers pulsing red. Text: "Most PCB mistakes are found after the boards come back." | Most PCB mistakes are found after the boards come back. |
| 2 | `scene: title` | Logo pulses, "ClawBoard", one-line pitch, three chips: OpenClaw, Kimi K3.1 Pro, Cerebras. | ClawBoard is an OpenClaw agent that reviews and repairs KiCad designs, running Kimi K3.1 Pro on Cerebras. |
| 3 | `CAPTURE board` | Live dashboard before the run: the broken ESP32-S3 board with its red markers. | This ESP32 board hides six classic bugs: shorted USB-C pins, a missing decoupling capacitor, no I2C pull-ups, a hair-thin power trace, a clearance violation and an unrouted net. |
| 4 | `CAPTURE fix` | Live run, cut in a few seconds before the patch: the model writes the patch, operations land one by one, new copper glows green, the error bars step down. | The agent inspects the project, runs the audit, and sends one patch: {ops} operations, each re-checked as it lands. {errors} errors, down to zero. |
| 5 | `CAPTURE verify` | Same run, finished: all three verification rungs green, the agent's sign-off. | Then KiCad's own rule checker confirms it: zero errors, nothing unconnected. |
| 6 | `scene: numbers` | Four tiles count up: tokens per second, model calls, patch operations, prompt-cache share. Proof line: errors before and after, KiCad DRC / unconnected / ERC. | On Cerebras, Kimi generated at {tps} tokens per second. The whole review took {calls} model calls. |
| 7 | `scene: close` | Logo, "ClawBoard", tagline, repo URL, #KimiOnCerebras. | ClawBoard. Catch it before the fab does. |

## Music cue

Procedural bed (`music.py`), side-chain ducked under the narration. Gentle swell under the
title (scene 2) and again under the numbers (scene 6); fade out over the last 1.3 s.

## Human shot list

None. There are no SUPPLY scenes in this cut: the product footage is recorded by
`capture_demo.py`, and there are no photos.

## Pronunciation

Respelled in the spoken text only: KiCad ("Key-CAD"), ESP32 ("E S P thirty-two"),
I2C ("I squared C"), K3.1 ("K three point one").

## What was left out, and why

- The side-by-side "Cerebras vs. Standard GPU" comparison: no baseline endpoint was configured,
  so there is no measured ratio to show.
- End-to-end elapsed time: during recording the Cerebras trial endpoint was returning
  "high traffic" 429s, so wall time mostly measured its queue. The film shows generation speed,
  which the provider reports per request, and leaves elapsed time to `report.json`.
- A longer full cut, photos and third-party music: out of scope for a 60-second submission.
