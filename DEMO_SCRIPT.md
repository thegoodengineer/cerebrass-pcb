# ClawBoard - 45-second recording script

One take, one browser window, no cuts needed. Target length 45 s (hard limit 60 s).

## Before you press record

**Secrets and privacy**

- [ ] Keys live only in `.env`. Close every editor tab that shows `.env`, and any terminal whose scrollback contains a key (`clear` is not enough: open a fresh terminal).
- [ ] The dashboard never displays keys. Confirm the status strip shows only the model id and host.
- [ ] Do not open `report.json` on camera unless you have checked it: it contains hosts, model ids and timings, no keys.
- [ ] Record a **single browser window**, not the whole screen. Use a clean browser profile or a guest window: no bookmarks bar, no other tabs, no extensions with badges, no profile avatar.
- [ ] Turn on Do Not Disturb / Focus Assist. Quit mail, chat and calendar apps.
- [ ] Hide the taskbar/dock, or crop it out. Check that the window title shows only "ClawBoard".

**Setup**

- [ ] `npm run dev`, open `http://localhost:3000`, browser zoom 100 %, window 1920 x 1080 (or 1600 x 900).
- [ ] **Simulated is unticked.** A recording with the `SIMULATED` watermark is a rehearsal, not a result.
- [ ] Status strip reads `LIVE endpoints`, the Cerebras model id, and your baseline model and host.
- [ ] Engine shows `OpenClaw harness` (needs the OpenClaw CLI and Node 24.16+). If it says `built-in loop`, the hackathon's OpenClaw requirement is not on screen: fix that first.
- [ ] Status strip shows `OpenClaw gateway: ready`. After `npm run dev` it warms up for one to three minutes; do not start a run before it is ready.
- [ ] Do one full warm-up run, then press **Reset**. This warms the prompt cache and confirms both endpoints answer.
- [ ] Wait at least 60 s after the warm-up so the per-minute token budget is full again.
- [ ] Viewer on **PCB**, layer **F.Cu**, zoom reset (**Fit**).

## Shot list

With OpenClaw there are a few seconds of harness setup on each lane before its first model
call (about 5-15 s on the left lane, about 5 s on the right, which is released when the left
lane is through setup). **Total elapsed** on each lane starts at that lane's first model
request, so the timers stay fair. Timings below are from a rehearsal on the development laptop.

| Time | On screen | Action | Say (optional voice-over) |
|---|---|---|---|
| 0:00 - 0:04 | Idle dashboard, broken board, red markers pulsing | Hold still. Move the pointer over a red marker so its tooltip shows. | "This ESP32-S3 board has six classic bugs: shorted USB-C CC pins, a missing decoupling cap, no I2C pull-ups, a hair-thin power trace, a clearance violation and an unrouted net." |
| 0:04 - 0:06 | Top bar | Click **Cerebras vs. Standard GPU**. | "Same OpenClaw agent, same tools, same board. Left is Kimi K3.1 Pro on Cerebras, right is a standard GPU endpoint." |
| 0:06 - 0:16 | Left lane shows `setup` | Keep talking through the setup. | "OpenClaw inspects the KiCad project, runs the DFM and ERC audit, and sends one patch." |
| 0:16 - 0:24 | Left lane runs and finishes | Let the green traces draw in on the left board: new resistors, the widened VBUS rail, UART_TX routed. The error bars step down to zero. | "Each operation is re-audited as it lands. Eight errors, then zero." |
| 0:24 - 0:38 | Right lane working | Hover the left board's glowing VBUS trace while the right lane is still generating. Glance at both **LLM time** tiles. | "The tools take the same time on both sides. The difference is how long each waits on the model." |
| 0:38 - 0:42 | Verdict banner appears | Hold on the banner and the two **Total elapsed** tiles. | Read the number on the banner. |
| 0:42 - 0:45 | Verification ladder | Point at rung 3 on the left lane once it turns green. | "And KiCad's own DRC agrees: zero errors, zero unconnected." |

If the baseline is slow enough that the banner would land after 0:45, do not speed up the
footage. Either cut to the moment the banner appears and put a visible "time skipped" caption
on the cut, or pick a faster baseline. The same goes for the setup wait at 0:06: cutting it is
fine if the caption says so. Rung 3 (KiCad) takes 15-40 s after a lane finishes and is outside
the timed run; if it has not turned green by the end, show it in a separate clip and say so.

## After recording

- [ ] Watch the take at full size. Look for keys, e-mail addresses, file paths with your name, notification pop-ups and other tabs.
- [ ] Check that the `SIMULATED` watermark is absent and the strip says `LIVE endpoints`.
- [ ] Save both `report.json` files (the link on each lane). They are the evidence for every number in the video.
- [ ] State the baseline model and endpoint in the video description, and that tool time is identical on both lanes.

## Fallbacks

| Problem | What to do |
|---|---|
| A lane shows an HTTP 429 | You hit the per-minute token limit. Wait 60 s, **Reset**, run again. |
| The agent leaves errors after its first patch | Keep recording: it sends a second patch. If it signs off with `FAIL`, that is the honest result for that run. |
| Board viewer stays empty | `python pcb-tools/cli.py audit sample_board` in a terminal, then **Reset**. |
| Gateway badge shows an error, or a lane fails to start | Restart `npm run dev` and wait for `OpenClaw gateway: ready`. Check the config with `openclaw config validate` and `openclaw mcp doctor clawboard-pcb --probe`. Switch Engine to **Built-in loop** only for debugging, not for the submission take. |
| Left lane sits in `setup` for more than 30 s | The gateway was still warming up. Let the run finish, **Reset**, and run again. |
