# Recording README GIFs

How the hero GIF (`docs/check-fix-green.gif`) was made, and the recipe for the three shots still to record.
Everything runs against a local eitri on a throwaway workspace, so your real projects are never touched.

## 1. Start a clean instance

```sh
export REC=/tmp/eitri-rec                       # throwaway: workspace, config, video
EITRI_CONFIG_DIR=$REC/config eitri web $REC/ws --port 8870 --no-open
```

- Open http://127.0.0.1:8870/ in a browser window sized **1280 x 800** (the GIFs are scaled from that).
- Demos live at `/p/demo-racket-holder/`, `/p/demo-v8-engine/`, `/p/demo-marble-maze/`.
- **Replays from a URL:** `/p/<demo>/?replay=1` starts the recorded session as soon as the page loads;
  add `&speed=N` (0.25 to 8; default 1) to play it faster or slower, e.g.
  `/p/demo-racket-holder/?replay=1&speed=2`. A replay makes no API calls.
- **Reset a demo** after editing it: the "Reset demo" button on its card (or `POST /p/<demo>/demo/reset`).
- Only shot (a) needs an API key (one real Claude request, about $0.10 to $0.30). Set it in this
  instance only: `printf '%s' "$KEY" | EITRI_CONFIG_DIR=$REC/config eitri key set`.

## 2. Capture

Any screen recorder works (macOS: Cmd-Shift-5 → "Record Selected Portion" around the browser viewport).
For a frame-exact, repeatable capture, Playwright records the page itself:

```python
# python -m pip install playwright && python -m playwright install chromium
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=False, args=["--use-gl=angle"])
    ctx = b.new_context(viewport={"width": 1280, "height": 800},
                        record_video_dir="/tmp/eitri-rec/video", record_video_size={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.goto("http://127.0.0.1:8870/p/demo-v8-engine/")
    page.pause()          # perform the shot by hand in the window, then resume in the inspector
    ctx.close()           # the .webm is written on close
```

Load each page once before recording (the first load warms the CAD kernel), and leave a second or two of
stillness at both ends so the cut points are easy to find.

## 3. The shots

### (a) Manual drag, then the agent continues  *(needs a key)*

1. Open `/p/demo-racket-holder/` (the finished hook, all checks green).
2. Start recording. Press **P** (Push/Pull), hover the hook's front face (the end of the arm), drag it out
   about 15 mm, release. The Revisions section (right panel) gains a row with the hand icon (your edit).
3. In the chat type: `Keep my longer hook, round the tip edges 2 mm and re-check the walls.` and send.
4. Wait until Claude's build lands: a new revision row with the sparkles icon (built by Claude), checks green again.
   Stop recording.
5. Reset the demo afterwards.

### (b) V8 exploded view

1. Open `/p/demo-v8-engine/`, wait for the model, click **Iso** in the view buttons.
2. Expand the **Display** section in the right panel.
3. Start recording. Drag the **explode** slider from 0 to the right end over about 3 seconds, pause,
   orbit the view slightly (left-drag), drag the slider back. Stop recording.

### (c) Print-export dialog with a part hovered

1. Open `/p/demo-v8-engine/`, wait for the model.
2. Start recording. Click the **printer icon** in the toolbar ("Export for 3D printing"). Wait until the
   dialog says **Plan up to date** ("V8 engine kit → 43 printable parts, 2 plates").
3. Move the pointer slowly down the parts list: each hovered row lights up its part on the plate
   (hovering a part on the plate highlights its row). Click "Plate 2" to switch plates once. Stop recording.

## 4. Convert to GIF

Two-pass ffmpeg (palettegen, then paletteuse), the same settings as the hero GIF. Set the variables,
then run the pipeline. `SEGS` is a list of `start:end` pairs in seconds of the source video; they are
cut and joined in order, and the last frame is held for `HOLD` seconds.

```sh
IN=/tmp/eitri-rec/video/page@xxxx.webm   # the recording
OUT=docs/v8-explode.gif
WIDTH=960 FPS=12 COLORS=48 HOLD=3
SEGS="2.0:9.5"                           # e.g. the hero GIF: "9.0:11.5 13.2:17.2 23.2:26.7 49.0:53.0 53.0:55.5"

segs=($SEGS); n=${#segs[@]}; g="[0:v]split=$n"; cat=""
for i in $(seq 0 $((n-1))); do g+="[s$i]"; done; g+=";"
for i in $(seq 0 $((n-1))); do
  a=${segs[$i]%%:*}; b=${segs[$i]##*:}
  g+="[s$i]trim=start=$a:end=$b,setpts=PTS-STARTPTS[t$i];"; cat+="[t$i]"
done
g+="${cat}concat=n=$n:v=1:a=0,fps=$FPS,scale=$WIDTH:-1:flags=lanczos,tpad=stop_mode=clone:stop_duration=$HOLD,"
g+="split[p][q];[p]palettegen=max_colors=$COLORS:stats_mode=diff[pal];[q][pal]paletteuse=dither=none:diff_mode=rectangle"
ffmpeg -y -i "$IN" -filter_complex "$g" -loop 0 "$OUT"
```

Run it in bash (`bash` first if your shell is zsh: zsh does not split `$SEGS` into an array this way).

To find cut points, make a contact sheet (one frame per second, 6 x 4 per image, reading order):

```sh
ffmpeg -i "$IN" -vf "fps=1,scale=420:-1,tile=6x4" /tmp/eitri-rec/sheet%d.png
```

Size targets for the README: 15 to 20 s, under 3 MB. If it comes out larger, lower `COLORS` (64 → 48)
before lowering `FPS` or `WIDTH`; flat UI colours survive a small palette well, and `dither=none` keeps
text crisp and compresses better than dithering. The hero GIF: 960 x 600, 12 fps, 48 colours, 19.6 s,
2.9 MB.

When a GIF is ready, add it to the README and delete its line from the "STILL TO RECORD" comment at the top.
