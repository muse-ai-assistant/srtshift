# srtshift ⏱️

Shift subtitle timelines and convert between **srt ⇄ ass ⇄ vtt**. Stdlib only, zero dependencies.

字幕时间轴平移 + 格式互转。纯标准库，零依赖。

## Why

- Subtitle is 2 seconds behind the audio? `srtshift sub.srt +2`
- Movie re-encoded from 23.976fps to 25fps and subs drift? `--set-fps/--to-fps`
- Phone player won't play `.ass`? Convert to `.srt` in one command.

字幕和声音对不上、帧率转制导致漂移、手机播放器不认 ass——一个命令搞定。

## Install

```bash
pip install .
# or just run it directly: python3 srtshift.py
```

## Usage

```bash
# shift timeline by +2.5 seconds
srtshift sub.srt +2.5

# shift by -1500 milliseconds / timecode style also works
srtshift sub.srt -1500ms
srtshift sub.srt +00:00:01,500

# fps conversion (e.g. 23.976 -> 25)
srtshift sub.srt --set-fps 23.976 --to-fps 25

# format conversion (output format from -o extension)
srtshift sub.ass -o sub.srt
srtshift sub.srt -o sub.vtt

# preview first 5 entries without writing
srtshift sub.srt +2.5 --dry-run

# batch
srtshift *.srt +1.2
```

Output is always UTF-8 (input auto-detected: utf-8 / utf-16 / gbk).
Negative results are clamped to `00:00:00,000`.
`ass → srt` drops styling but keeps all text (`{\...}` tags stripped, `\N` → newline).

## 示例

```bash
$ srtshift movie.srt +2.5 --dry-run
== movie.srt (srt -> srt, 812 entries)
   00:00:01,000 --> 00:00:04,000  =>  00:00:03,500 --> 00:00:06,500
   ...

$ srtshift movie.ass -o movie.srt
ok movie.ass -> movie.srt (812 entries, ass->srt)
done: 1 file(s), 812 entries
```

## License

MIT
