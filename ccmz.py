#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析 .ccmz 琴谱文件并导出标准 MIDI。

.ccmz 是一个带版本号的 ZIP 容器：

    v1: 第 1 字节 0x01，后接 ZIP，内含 data.xml + data.mid
    v2: 第 1 字节 0x02，后接逐字节 XOR 0x01 的 ZIP，内含 score.json + midi.json

用法：

    python ccmz.py song.ccmz
    python ccmz.py song.ccmz -o output/song.mid
    python ccmz.py song.ccmz --score --json
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from collections import Counter, defaultdict, deque
from pathlib import Path

from midiutil.MidiFile import FLATS, SHARPS, MIDIFile

DEFAULT_PPQ = 480
MAX_TRACKS = 256


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _clamp(value, low, high, default=0):
    """把 value 转成 int 并限制在 [low, high] 内。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _detect_ppq(data, override=None):
    """从 midi.json 中推断 PPQ（division），找不到时回退到 480。"""
    if override is not None:
        value = int(override)
        if value <= 0:
            raise ValueError("PPQ 必须是正整数")
        return value

    header = data.get("header")
    sources = [data]
    if isinstance(header, dict):
        sources.append(header)

    for source in sources:
        for key in ("ppq", "ticksPerBeat", "ticks_per_beat", "ticksPerQuarter", "division"):
            value = source.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            value = int(value)
            # 0x8000 以上是 SMPTE 时间格式，不是 PPQ
            if 0 < value < 0x8000:
                return value
    return DEFAULT_PPQ


def _tempo_to_bpm(value):
    """兼容两种常见写法：微秒/四分音符（500000）或 BPM（120）。"""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if value <= 1000:          # 120 这种直接就是 BPM
        bpm = value
    else:                      # 500000 这种是微秒/四分音符
        bpm = 60000000.0 / value
    if not 1.0 <= bpm <= 1000.0:
        return None
    return bpm


def _decode_text(payload):
    """meta 文本事件一般是 UTF-8，少数文件是 Latin-1。"""
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload.decode("latin-1", errors="replace")


def _decode_payload(payload):
    """解压出来的 XML/JSON 文本，兼容 UTF-8 BOM 和 GB18030。"""
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return payload.decode("gb18030", errors="replace")


def _safe_text(text):
    """midiutil 用 latin-1 写文本事件，先把非 latin-1 字符替换掉。"""
    return "".join(ch if ord(ch) < 256 else "?" for ch in str(text))


def _new_midifile(track_count, ppq):
    """优先使用「按 tick 计时」的写法，这样 PPQ 和 tick 都能原样保留。

    老版本 midiutil 没有 eventtime_is_ticks 参数，则退回「按拍计时」。
    """
    try:
        midi = MIDIFile(
            track_count,
            removeDuplicates=False,
            file_format=1,
            ticks_per_quarternote=ppq,
            eventtime_is_ticks=True,
        )
        return midi, True
    except TypeError:
        midi = MIDIFile(track_count, removeDuplicates=False)
        return midi, False


# ---------------------------------------------------------------------------
# 数据容器
# ---------------------------------------------------------------------------

class CCMZ:
    """解析结果。

    version: 1 或 2
    score:   v1 为 data.xml 文本，v2 为 score.json 文本
    midi:    v1 为 data.mid 原始字节，v2 为 midi.json 文本
    """

    def __init__(self):
        self.version = None
        self.score = None
        self.midi = None

    @property
    def ver(self):
        """兼容旧代码里的 info.ver 写法。"""
        return self.version


# ---------------------------------------------------------------------------
# 主库
# ---------------------------------------------------------------------------

class LibCCMZ:
    # ------------------------------------------------------------------
    # .ccmz 容器
    # ------------------------------------------------------------------
    @staticmethod
    def read_ccmz(buffer, callback=None):
        """读取 .ccmz 字节流，返回 CCMZ；callback 仅为兼容旧调用保留。"""
        if not buffer:
            raise ValueError("ccmz 数据为空")

        buffer = bytes(buffer)
        info = CCMZ()
        info.version = buffer[0]
        payload = buffer[1:]

        if info.version == 1:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                info.score = _decode_payload(LibCCMZ._read_member(archive, "data.xml"))
                info.midi = LibCCMZ._read_member(archive, "data.mid")
        elif info.version == 2:
            # 原始数据逐字节 XOR 0x01，解出来才是 ZIP
            payload = bytes(byte ^ 0x01 for byte in payload)
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                info.score = _decode_payload(LibCCMZ._read_member(archive, "score.json"))
                info.midi = _decode_payload(LibCCMZ._read_member(archive, "midi.json"))
        else:
            raise ValueError(f"不支持的 ccmz 版本字节: 0x{info.version:02X}（只支持 1、2）")

        if callback is not None:
            callback(info)
        return info

    @staticmethod
    def _read_member(archive, name):
        try:
            return archive.read(name)
        except KeyError:
            names = "、".join(archive.namelist()) or "（空）"
            raise ValueError(f"ccmz 中缺少 {name}，实际包含: {names}") from None

    # ------------------------------------------------------------------
    # MIDI 事件解析
    # ------------------------------------------------------------------
    @staticmethod
    def _read_varlen(data, pos):
        """读取 MIDI 的可变长度整数（VLQ），返回 (值, 新位置)。"""
        value = 0
        while pos < len(data):
            byte = data[pos]
            pos += 1
            value = (value << 7) | (byte & 0x7F)
            if not byte & 0x80:
                return value, pos
        raise ValueError("VLQ 数据被截断")

    @staticmethod
    def parse_midi_event(event_bytes, running_status=None):
        """把一个事件的字节序列解析成 dict。

        event_bytes 通常是完整事件（含 status）；如果首字节 < 0x80，
        说明数据里用了 running status，需要用 running_status 补上状态字节。
        无法识别时返回 None。
        """
        if not event_bytes:
            return None

        try:
            raw = bytes(event_bytes)
        except (TypeError, ValueError):
            return None
        if not raw:
            return None

        status = raw[0]
        if status < 0x80:
            if running_status is None or running_status >= 0xF0:
                return None
            raw = bytes((running_status,)) + raw
            status = running_status

        if status < 0xF0:
            return LibCCMZ._parse_channel_event(raw, status)
        if status == 0xFF:
            return LibCCMZ._parse_meta_event(raw)
        if status in (0xF0, 0xF7):
            return LibCCMZ._parse_sysex_event(raw, status)
        return None

    @staticmethod
    def _parse_channel_event(raw, status):
        if len(raw) < 2:
            return None

        event_type = status >> 4
        channel = status & 0x0F
        event = {"type": "channel", "channel": channel}

        if event_type == 0x8:                       # Note Off
            event["subtype"] = "noteOff"
            event["noteNumber"] = raw[1]
            if len(raw) > 2:
                event["velocity"] = raw[2]
        elif event_type == 0x9:                     # Note On / 力度 0 视为 Note Off
            event["noteNumber"] = raw[1]
            velocity = raw[2] if len(raw) > 2 else 0
            event["velocity"] = velocity
            event["subtype"] = "noteOn" if velocity > 0 else "noteOff"
        elif event_type == 0xA:                     # Polyphonic Aftertouch
            event["subtype"] = "noteAftertouch"
            event["noteNumber"] = raw[1]
            if len(raw) > 2:
                event["amount"] = raw[2]
        elif event_type == 0xB:                     # Controller
            event["subtype"] = "controller"
            event["controllerType"] = raw[1]
            if len(raw) > 2:
                event["value"] = raw[2]
        elif event_type == 0xC:                     # Program Change
            event["subtype"] = "programChange"
            event["programNumber"] = raw[1]
        elif event_type == 0xD:                     # Channel Aftertouch
            event["subtype"] = "channelAftertouch"
            event["amount"] = raw[1]
        elif event_type == 0xE:                     # Pitch Bend（0..16383，中心 8192）
            event["subtype"] = "pitchBend"
            event["value"] = raw[1] + ((raw[2] << 7) if len(raw) > 2 else 0)
        else:
            event["subtype"] = "unknown"

        return event

    @staticmethod
    def _parse_meta_event(raw):
        if len(raw) < 2:
            return None

        meta_type = raw[1]
        try:
            length, pos = LibCCMZ._read_varlen(raw, 2)
        except ValueError:
            return None
        if pos + length > len(raw):
            return None
        payload = raw[pos:pos + length]

        event = {"type": "meta", "metaType": meta_type}

        if meta_type == 0x00:
            event["subtype"] = "sequenceNumber"
            if len(payload) == 2:
                event["number"] = (payload[0] << 8) | payload[1]
        elif meta_type in (0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07):
            event["subtype"] = {
                0x01: "text",
                0x02: "copyrightNotice",
                0x03: "trackName",
                0x04: "instrumentName",
                0x05: "lyrics",
                0x06: "marker",
                0x07: "cuePoint",
            }[meta_type]
            event["text"] = _decode_text(payload)
        elif meta_type == 0x20:
            event["subtype"] = "midiChannelPrefix"
            if len(payload) == 1:
                event["channel"] = payload[0]
        elif meta_type == 0x2F:
            event["subtype"] = "endOfTrack"
        elif meta_type == 0x51:
            event["subtype"] = "setTempo"
            if len(payload) == 3:
                event["microsecondsPerBeat"] = (
                    (payload[0] << 16) | (payload[1] << 8) | payload[2]
                )
        elif meta_type == 0x54:
            event["subtype"] = "smpteOffset"
            if len(payload) == 5:
                first = payload[0]
                event["frameRate"] = {0: 24, 32: 25, 64: 29, 96: 30}.get(first & 0x60, 30)
                event["hour"] = first & 0x1F
                event["min"] = payload[1]
                event["sec"] = payload[2]
                event["frame"] = payload[3]
                event["subframe"] = payload[4]
        elif meta_type == 0x58:
            event["subtype"] = "timeSignature"
            if len(payload) == 4:
                event["numerator"] = payload[0]
                event["denominatorPow"] = payload[1]   # 2 表示四分音符
                event["denominator"] = 2 ** payload[1]
                event["metronome"] = payload[2]
                event["thirtyseconds"] = payload[3]
        elif meta_type == 0x59:
            event["subtype"] = "keySignature"
            if len(payload) == 2:
                key = payload[0]
                # 调号是 8 位有符号数：正数升号，负数降号
                event["key"] = key - 256 if key > 127 else key
                event["scale"] = payload[1]
        elif meta_type == 0x7F:
            event["subtype"] = "sequencerSpecific"
            event["data"] = payload
        else:
            event["subtype"] = "unknown"
            event["data"] = payload

        return event

    @staticmethod
    def _parse_sysex_event(raw, status):
        try:
            length, pos = LibCCMZ._read_varlen(raw, 1)
        except ValueError:
            return None
        if pos + length > len(raw):
            return None
        return {
            "type": "sysEx" if status == 0xF0 else "dividedSysEx",
            "data": raw[pos:pos + length],
        }

    # ------------------------------------------------------------------
    # 重建 MIDI
    # ------------------------------------------------------------------
    @staticmethod
    def write_midi(data, output, ppq=None, stats=None):
        """把 midi.json 的内容写成标准 .mid 文件。

        data:   json.loads(midi.json)
        output: 输出路径
        ppq:    覆盖 PPQ；默认从 data 中识别，再不行用 480
        stats:  可选 dict，会被填入统计信息
        """
        if not isinstance(data, dict):
            raise ValueError("midi.json 的顶层必须是对象")

        tracks = data.get("tracks") or []
        events = data.get("events") or []
        if not isinstance(tracks, list) or not isinstance(events, list):
            raise ValueError("tracks / events 字段格式不正确")
        if not tracks and not events:
            raise ValueError("midi.json 中没有 tracks/events 数据")

        ppq = _detect_ppq(data, ppq)

        # ---- 1. 解析事件（running status 按轨道分别维护） ----
        parsed = []
        last_status = {}
        used_tracks = []

        for index, item in enumerate(events):
            if not isinstance(item, dict):
                continue

            track = _clamp(item.get("track", 0), 0, MAX_TRACKS - 1, 0)
            used_tracks.append(track)

            raw = item.get("event")
            if not isinstance(raw, (bytes, bytearray, list, tuple)):
                continue
            try:
                raw = bytes(raw)
            except (TypeError, ValueError):
                continue
            if not raw:
                continue

            current_status = raw[0]
            event = LibCCMZ.parse_midi_event(raw, last_status.get(track))
            # 只有真正的状态字节才更新 running status；数据字节不能覆盖它
            if current_status >= 0x80:
                last_status[track] = current_status if current_status < 0xF0 else None

            if not event:
                continue

            event["tick"] = _clamp(item.get("tick", 0), 0, 2 ** 31 - 1, 0)
            event["duration"] = _clamp(item.get("duration", 0), 0, 2 ** 31 - 1, 0)
            event["track"] = track
            event["_index"] = index
            parsed.append(event)

        track_count = max(len(tracks), 1)
        for track in used_tracks:
            track_count = max(track_count, track + 1)
        track_count = min(track_count, MAX_TRACKS)

        midi, tick_mode = _new_midifile(track_count, ppq)

        def at(tick):
            """tick -> midiutil 需要的时间单位。"""
            return int(tick) if tick_mode else tick / ppq

        # ---- 2. 每条轨道的主通道（用于轨首的音色） ----
        channel_votes = defaultdict(Counter)
        for event in parsed:
            if event["type"] == "channel":
                channel_votes[event["track"]][event.get("channel", 0)] += 1
        track_channel = {
            track: votes.most_common(1)[0][0]
            for track, votes in channel_votes.items()
        }

        # ---- 3. 轨道名 / 默认音色 ----
        for index, track in enumerate(tracks[:track_count]):
            if isinstance(track, dict):
                name = track.get("name")
                if name:
                    midi.addTrackName(index, at(0), _safe_text(name))
            elif isinstance(track, str) and track:
                midi.addTrackName(index, at(0), _safe_text(track))

        # 事件里已经有 programChange 的轨道，不再重复写轨首音色
        tracks_with_program = {
            event["track"]
            for event in parsed
            if event.get("subtype") == "programChange"
        }
        for index, track in enumerate(tracks[:track_count]):
            if index in tracks_with_program:
                continue
            if not isinstance(track, dict) or "program" not in track:
                continue
            program = _clamp(track["program"], 0, 127, 0)
            midi.addProgramChange(index, track_channel.get(index, 0), at(0), program)

        # ---- 4. 速度表（优先用 tempos 字段，没有就从 setTempo 事件里找） ----
        tempo_entries = []
        for item in data.get("tempos") or []:
            if not isinstance(item, dict):
                continue
            raw_tempo = item.get(
                "tempo", item.get("microsecondsPerBeat", item.get("value"))
            )
            bpm = _tempo_to_bpm(raw_tempo)
            if bpm is None:
                continue
            tick = _clamp(item.get("tick", item.get("time", 0)), 0, 2 ** 31 - 1, 0)
            tempo_entries.append((tick, bpm))

        if not tempo_entries:
            for event in parsed:
                if event.get("subtype") != "setTempo":
                    continue
                bpm = _tempo_to_bpm(event.get("microsecondsPerBeat"))
                if bpm is not None:
                    tempo_entries.append((event["tick"], bpm))

        # 去重后按 tick 排序，变速会完整写进 tempo 轨
        for tick, bpm in sorted(set(tempo_entries), key=lambda pair: pair[0]):
            midi.addTempo(0, at(tick), bpm)

        # ---- 5. 拍号 / 调号 ----
        for event in parsed:
            subtype = event.get("subtype")
            if subtype == "timeSignature":
                numerator = _clamp(event.get("numerator", 4), 1, 255, 4)
                denominator_pow = _clamp(event.get("denominatorPow", 2), 0, 7, 2)
                clocks = _clamp(event.get("metronome", 24), 0, 255, 24)
                notes = _clamp(event.get("thirtyseconds", 8), 0, 255, 8)
                midi.addTimeSignature(
                    0, at(event["tick"]), numerator, denominator_pow, clocks, notes
                )
            elif subtype == "keySignature":
                key = _clamp(event.get("key", 0), -7, 7, 0)
                mode = _clamp(event.get("scale", 0), 0, 1, 0)
                midi.addKeySignature(
                    0, at(event["tick"]), abs(key), SHARPS if key >= 0 else FLATS, mode
                )

        # ---- 6. 音符与通道事件 ----
        pending = defaultdict(deque)
        counters = {
            "notes": 0,
            "orphan_note_off": 0,
            "unterminated": 0,
            "aftertouch": 0,
        }

        def add_note(note_on, duration):
            midi.addNote(
                note_on["track"],
                note_on.get("channel", 0),
                _clamp(note_on.get("noteNumber", 60), 0, 127, 60),
                at(note_on["tick"]),
                at(max(1, int(duration))),
                _clamp(note_on.get("velocity", 90), 1, 127, 90),
            )
            counters["notes"] += 1

        for event in sorted(parsed, key=lambda item: (item["tick"], item["_index"])):
            if event["type"] != "channel":
                continue

            subtype = event.get("subtype")
            track = event["track"]
            channel = event.get("channel", 0)
            tick = event["tick"]

            if subtype == "noteOn":
                key = (track, channel, event.get("noteNumber", 60))
                pending[key].append(event)

            elif subtype == "noteOff":
                key = (track, channel, event.get("noteNumber", 60))
                queue = pending.get(key)
                if queue:
                    note_on = queue.popleft()
                    add_note(note_on, tick - note_on["tick"])
                else:
                    counters["orphan_note_off"] += 1

            elif subtype == "controller":
                midi.addControllerEvent(
                    track,
                    channel,
                    at(tick),
                    _clamp(event.get("controllerType", 0), 0, 127, 0),
                    _clamp(event.get("value", 0), 0, 127, 0),
                )

            elif subtype == "programChange":
                midi.addProgramChange(
                    track,
                    channel,
                    at(tick),
                    _clamp(event.get("programNumber", 0), 0, 127, 0),
                )

            elif subtype == "pitchBend":
                # midiutil 要的是 -8192..8191，声明的值要减去中心点
                value = _clamp(event.get("value", 8192), 0, 16383, 8192) - 8192
                midi.addPitchWheelEvent(track, channel, at(tick), value)

            elif subtype in ("noteAftertouch", "channelAftertouch"):
                # midiutil 没有触后 API，先计数跳过
                counters["aftertouch"] += 1

        # 没有 noteOff 的音符：优先用它自带的 duration，仍没有就给半拍
        for queue in pending.values():
            for note_on in queue:
                duration = note_on.get("duration", 0)
                if duration <= 0:
                    duration = max(1, ppq // 2)
                    counters["unterminated"] += 1
                add_note(note_on, duration)

        # ---- 7. 写文件 ----
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "wb") as file:
            midi.writeFile(file)

        if stats is not None:
            stats.update(counters)
            stats.update(
                {
                    "ppq": ppq,
                    "tracks": track_count,
                    "events": len(events),
                    "parsed": len(parsed),
                }
            )

        return str(output)


# ---------------------------------------------------------------------------
# 命令行（只处理本地 .ccmz）
# ---------------------------------------------------------------------------

def _build_parser():
    parser = argparse.ArgumentParser(
        prog="ccmz.py",
        description="解析本地 .ccmz 琴谱文件并导出标准 MIDI",
    )
    parser.add_argument("source", help=".ccmz 文件路径")
    parser.add_argument(
        "-o", "--output",
        help="输出 .mid 路径或目录（默认与输入文件同目录、同名）",
    )
    parser.add_argument(
        "--score", action="store_true",
        help="同时导出谱面（v1 为 data.xml，v2 为 score.json）",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="导出解包后的 midi.json（仅 v2）",
    )
    parser.add_argument(
        "--ppq", type=int,
        help="覆盖 PPQ/division（默认从 midi.json 自动识别，最后回退 480）",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="只输出错误信息")
    return parser


def _resolve_output(source, output):
    if not output:
        return source.with_suffix(".mid")

    path = Path(output).expanduser()
    if path.is_dir() or str(output).endswith(("/", "\\")):
        return path / f"{source.stem}.mid"
    if path.suffix:
        return path
    return path.with_suffix(".mid")


def _run(args):
    source = Path(args.source).expanduser()
    if not source.is_file():
        raise ValueError(f"文件不存在: {source}")

    info = LibCCMZ.read_ccmz(source.read_bytes())
    output = _resolve_output(source, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    stats = {}
    if info.version == 1:
        output.write_bytes(info.midi)
    else:
        midi_data = json.loads(info.midi)
        LibCCMZ.write_midi(midi_data, output, ppq=args.ppq, stats=stats)

    if not args.quiet:
        print(f"版本: v{info.version}")
        if stats:
            print(
                f"PPQ: {stats['ppq']}  轨道: {stats['tracks']}  "
                f"事件: {stats['parsed']}/{stats['events']}  音符: {stats['notes']}"
            )
            if stats["orphan_note_off"]:
                print(f"提示: 忽略了 {stats['orphan_note_off']} 个找不到 noteOn 的 noteOff")
            if stats["unterminated"]:
                print(
                    f"提示: {stats['unterminated']} 个 noteOn 缺少 noteOff，"
                    f"已按自带 duration 或默认半拍输出"
                )
            if stats["aftertouch"]:
                print(f"提示: 跳过了 {stats['aftertouch']} 个触后事件（midiutil 不支持）")
        print(f"已保存 MIDI: {output}")

    if args.score:
        suffix = ".score.json" if info.version == 2 else ".score.xml"
        score_path = output.with_suffix(suffix)
        score_path.write_text(info.score, encoding="utf-8")
        if not args.quiet:
            print(f"已保存谱面: {score_path}")

    if args.json and info.version == 2:
        json_path = output.with_suffix(".midi.json")
        json_path.write_text(info.midi, encoding="utf-8")
        if not args.quiet:
            print(f"已保存 midi.json: {json_path}")

    return output


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        _run(args)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
