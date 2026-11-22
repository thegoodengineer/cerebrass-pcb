# ClawBoard demo film

Everything in the film is generated from this folder; no video editor was used and nothing is
sped up. The product footage is one live run (real OpenClaw harness, real Kimi K3.1 Pro on
Cerebras, real PCB tools), and every number on screen or in the narration is read from that
run's `assets/report.json`.

| Step | Tool | Output |
|---|---|---|
| Script | `film.py` (scene table, narration, timing) and `script.md` | the scene list |
| Product demo | `capture_demo.py`: Playwright drives the running dashboard at 1920x1080 and logs state milestones every 0.5 s | `assets/demo.webm`, `assets/demo.webm.json`, `assets/report.json` |
| Narration | `narrate.py`: edge-tts (`en-US-AndrewNeural`), falling back to the offline Windows voice when the service is unreachable | `vo/NN.wav`, `vo/NN.txt` |
| Motion graphics | `scenes/scenes.html?scene=<id>` in the product's own palette and fonts, recorded by `render_scene.py` / `render_all.py` (headless Chrome) | `renders/sNN.webm` |
| Music | `music.py`: procedural ambient bed (numpy), original, no licence needed | `assets/music.wav` |
| Assembly | `assemble.py`: ffmpeg only. 0.7 s cross-fades, narration ducking the music, loudness normalised | `clawboard_demo.mp4` |

Run order (dashboard running with `npm run dev`, `CEREBRAS_API_KEY` in `.env`, gateway ready):

```bash
cd video
python capture_demo.py      # records a live run; refuses to record a simulated one
python narrate.py           # regenerate single lines: python narrate.py 4 6
python music.py
python render_all.py
python assemble.py          # prints the total; fails if it is over 60 s
```

Requirements: `pip install playwright edge-tts imageio-ffmpeg numpy`, `playwright install ffmpeg`,
and Google Chrome (Playwright uses the installed browser).

The capture scenes are cut at milestones from `assets/demo.webm.json` (board visible, patch
tool started, KiCad oracle finished). Cuts are allowed; time-stretching is not. Waiting that is
cut out (harness setup, the model's queue time) is still in the raw `assets/demo.webm`.

There are no photos, no third-party music and no human-recorded shots in this cut.
