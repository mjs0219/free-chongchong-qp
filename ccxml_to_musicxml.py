# -*- coding: utf-8 -*-
"""CCXML -> MusicXML 转换器。

CCXML 是 .ccmz v2 中 score.json 的结构；这里把它转换成
Lilypond（musicxml2ly）能够读取的 MusicXML。

转换逻辑参考 bszapp/ccmz-to-midi 的 ccxml-to-musicxml 分支，
但重写了小节内的时间轴、和弦、附点和多声部处理。
"""

import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from xml.dom import minidom

# MusicXML 的 divisions。CCXML 的 tick 也是 480/四分音符，
# 直接令 divisions=480，时值和 tick 可以原样使用，避免取整误差。
DIVISIONS = 480

# CCXML 的 type 是音符时值分母：1=全音符，4=四分音符，8=八分音符……
TYPE_TICKS = {
    1: 1920,
    2: 960,
    4: 480,
    8: 240,
    16: 120,
    32: 60,
    64: 30,
}


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _pitch_of(element):
    """CCXML 的 step/octave/alter -> MIDI 音高。"""
    step = _to_int(element.get("step"), 1)
    octave = _to_int(element.get("octave"), 4)
    alter = _to_int(element.get("alter"), 0)
    semitone = [0, 2, 4, 5, 7, 9, 11][step - 1] if 1 <= step <= 7 else 0
    return (octave + 1) * 12 + semitone + alter


class CCXMLToMusicXML:
    """把 CCXML 字典转换成 MusicXML 字符串。"""

    def __init__(self, ccxml_data, config=None):
        self.ccxml = ccxml_data or {}
        self.config = config or {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "enableShift": True,
            "fontScale": 0.9,
        }
        self.type_map = {
            1: "whole",
            2: "half",
            4: "quarter",
            8: "eighth",
            16: "16th",
            32: "32nd",
            64: "64th",
        }
        self.step_map = ["C", "D", "E", "F", "G", "A", "B"]
        self._last_fifths = None
        self._last_time = None
        self._last_clefs = {}

    # ------------------------------------------------------------------
    # 顶层
    # ------------------------------------------------------------------
    def convert(self):
        root = self._create_root()
        self._add_work(root)
        self._add_identification(root)
        self._add_defaults(root)
        self._add_credits(root)
        self._add_part_list(root)
        self._add_parts(root)
        return self._prettify(root)

    def _create_root(self):
        return ET.Element("score-partwise", {"version": "4.0"})

    def _add_work(self, root):
        work = ET.SubElement(root, "work")
        title = self._title().get("title", "")
        ET.SubElement(work, "work-title").text = str(title or "")

    def _add_identification(self, root):
        ident = ET.SubElement(root, "identification")

        creator = ET.SubElement(ident, "creator", {"type": "composer"})
        composer = str(self._title().get("composer") or "")
        creator.text = composer.replace("\r\n", " ").replace("\n", " ")

        encoding = ET.SubElement(ident, "encoding")
        ET.SubElement(encoding, "software").text = (
            "chongchong-free-python (ccxml-to-musicxml)"
        )
        ET.SubElement(encoding, "encoding-date").text = datetime.now().strftime("%Y-%m-%d")

        supports_list = [
            {"element": "accidental", "type": "yes"},
            {"element": "beam", "type": "yes"},
            {"element": "print", "attribute": "new-page", "type": "yes", "value": "yes"},
            {"element": "print", "attribute": "new-system", "type": "yes", "value": "yes"},
            {"element": "stem", "type": "yes"},
        ]
        for support in supports_list:
            ET.SubElement(encoding, "supports", support)

        misc = ET.SubElement(ident, "miscellaneous")
        misc_date = ET.SubElement(misc, "miscellaneous-field", {"name": "creationDate"})
        misc_date.text = self.config["date"]

        subtitle = ET.SubElement(misc, "miscellaneous-field", {"name": "subtitle"})
        subtitle.text = str(self._title().get("subtitle") or "")

        copyright_elem = ET.SubElement(misc, "miscellaneous-field", {"name": "copyright"})
        qrcode = self.ccxml.get("qrcode") or {}
        link = qrcode.get("link", "") if isinstance(qrcode, dict) else ""
        copyright_elem.text = (
            "本文件来自虫虫钢琴ccmz格式转换，版权归原作者所有\n"
            f"来源：{link}"
        )

    def _add_defaults(self, root):
        defaults = ET.SubElement(root, "defaults")

        scaling = ET.SubElement(defaults, "scaling")
        ET.SubElement(scaling, "millimeters").text = "6.99911"
        ET.SubElement(scaling, "tenths").text = "40"

        page = self.ccxml.get("page") or {}
        page_layout = ET.SubElement(defaults, "page-layout")
        ET.SubElement(page_layout, "page-height").text = str(page.get("h", 1700))
        ET.SubElement(page_layout, "page-width").text = str(page.get("w", 1200))

        for margin_type in ("even", "odd"):
            margins = ET.SubElement(page_layout, "page-margins", {"type": margin_type})
            for side in ("left-margin", "right-margin", "top-margin", "bottom-margin"):
                ET.SubElement(margins, side).text = "85.7252"

        appearance = ET.SubElement(defaults, "appearance")
        line_widths = [
            ("light barline", "1.8"), ("heavy barline", "5.5"), ("beam", "5"),
            ("bracket", "4.5"), ("dashes", "1"), ("enclosure", "1"),
            ("ending", "1.1"), ("extend", "1"), ("leger", "1.6"),
            ("pedal", "1.1"), ("octave shift", "1.1"), ("slur middle", "2.1"),
            ("slur tip", "0.5"), ("staff", "1.1"), ("stem", "1"),
            ("tie middle", "2.1"), ("tie tip", "0.5"), ("tuplet bracket", "1"),
            ("wedge", "1.2"),
        ]
        for line_type, width in line_widths:
            ET.SubElement(appearance, "line-width", {"type": line_type}).text = width

        ET.SubElement(appearance, "note-size", {"type": "cue"}).text = "70"
        ET.SubElement(appearance, "note-size", {"type": "grace"}).text = "70"
        ET.SubElement(appearance, "note-size", {"type": "grace-cue"}).text = "49"

        ET.SubElement(defaults, "music-font", {"font-family": "Leland"})
        ET.SubElement(defaults, "word-font", {"font-family": "Edwin", "font-size": "10"})
        ET.SubElement(defaults, "lyric-font", {"font-family": "Edwin", "font-size": "10"})

    def _add_credits(self, root):
        title = self._title()
        title_text = str(title.get("title") or "")
        composer_text = str(title.get("composer") or "")
        subtitle_text = str(title.get("subtitle") or "")

        credit = ET.SubElement(root, "credit", {"page": "1"})
        ET.SubElement(credit, "credit-type").text = "title"
        ET.SubElement(credit, "credit-words", {
            "default-x": "600.241935",
            "default-y": "1611.210312",
            "justify": "center",
            "valign": "top",
            "font-size": "22",
        }).text = title_text

        credit = ET.SubElement(root, "credit", {"page": "1"})
        ET.SubElement(credit, "credit-type").text = "composer"
        ET.SubElement(credit, "credit-words", {
            "default-x": "1114.7587",
            "default-y": "1511.210312",
            "justify": "right",
            "valign": "bottom",
        }).text = composer_text

        if subtitle_text:
            credit = ET.SubElement(root, "credit", {"page": "1"})
            ET.SubElement(credit, "credit-type").text = "subtitle"
            ET.SubElement(credit, "credit-words", {
                "default-x": "600.241935",
                "default-y": "1554.374677",
                "justify": "center",
                "valign": "top",
                "font-size": "14",
            }).text = subtitle_text

    def _add_part_list(self, root):
        part_list = ET.SubElement(root, "part-list")
        parts = self.ccxml.get("parts") or []
        lines = self.ccxml.get("lines") or []

        for index in range(len(parts)):
            pid = f"P{index + 1}"
            score_part = ET.SubElement(part_list, "score-part", {"id": pid})

            name = ""
            shortname = ""
            for line_index, line in enumerate(lines):
                for stave in line.get("lineStaves") or []:
                    if _to_int(stave.get("parti"), -1) != index:
                        continue
                    stave_name = str(stave.get("name") or "")
                    if line_index == 0 and not name:
                        name = stave_name
                    elif line_index == 1 and not shortname:
                        shortname = stave_name
                if name and shortname:
                    break

            if not name:
                name = "Piano"
            if not shortname:
                shortname = name

            ET.SubElement(score_part, "part-name").text = name
            ET.SubElement(score_part, "part-abbreviation").text = shortname

            instrument_id = f"{pid}-I1"
            score_instrument = ET.SubElement(
                score_part, "score-instrument", {"id": instrument_id}
            )
            ET.SubElement(score_instrument, "instrument-name").text = "Piano"
            ET.SubElement(score_instrument, "instrument-sound").text = "keyboard.piano"
            ET.SubElement(score_part, "midi-device", {"id": instrument_id, "port": "1"})

            midi_instrument = ET.SubElement(
                score_part, "midi-instrument", {"id": instrument_id}
            )
            ET.SubElement(midi_instrument, "midi-channel").text = str(index + 1)
            ET.SubElement(midi_instrument, "midi-program").text = "1"
            ET.SubElement(midi_instrument, "volume").text = "78.7402"
            ET.SubElement(midi_instrument, "pan").text = "0"

    def _add_parts(self, root):
        parts = self.ccxml.get("parts") or []
        for part_index, part_data in enumerate(parts):
            self._last_fifths = None
            self._last_time = None
            self._last_clefs = {}
            part = ET.SubElement(root, "part", {"id": f"P{part_index + 1}"})
            for measure_index, measure_data in enumerate(part_data.get("measures") or []):
                self._add_measure(part, measure_index, measure_data or {}, part_index)

    # ------------------------------------------------------------------
    # 小节
    # ------------------------------------------------------------------
    def _add_measure(self, part, measure_index, measure_data, part_index=0):
        measure = ET.SubElement(part, "measure", {"number": str(measure_index + 1)})

        staves = max(1, _to_int(measure_data.get("staves"), 1))
        notes = measure_data.get("notes") or []
        lines = self.ccxml.get("lines") or []
        line = next(
            (item for item in lines
             if _to_int(item.get("m1"), -1) == measure_index),
            None,
        )

        # 当前行各谱表的谱号（CCXML 在每个 system 开头给出 clef）
        clef_map = {}
        if line is not None:
            for stave in line.get("lineStaves") or []:
                if _to_int(stave.get("parti"), -1) != part_index:
                    continue
                raw_clefs = stave.get("clef") or {}
                if isinstance(raw_clefs, dict):
                    for staff_key, clef_name in raw_clefs.items():
                        clef_map[_to_int(staff_key, 0)] = str(clef_name)
                break

        # 源文件的 clef 只在 system 开头给出，个别小节内部会变；
        # 对双谱表的低音谱表按实际音高做一次保守修正。
        if staves > 1:
            for staff_number in range(2, staves + 1):
                pitches = [
                    _pitch_of(element)
                    for note in notes
                    if _to_int(note.get("staff"), 1) == staff_number
                    for element in (note.get("elems") or [])
                ]
                if not pitches:
                    continue
                if max(pitches) < 55:
                    clef_map[staff_number] = "Bass"
                elif min(pitches) > 60:
                    clef_map[staff_number] = "Treble"

        clef_changes = {
            staff: name
            for staff, name in clef_map.items()
            if staff >= 1 and self._last_clefs.get(staff) != name
        }

        if line is not None:
            print_attrs = {}
            if line.get("newpage"):
                print_attrs = {"new-page": "yes", "new-system": "yes"}
            elif measure_index > 0:
                print_attrs = {"new-system": "yes"}

            print_elem = ET.SubElement(measure, "print", print_attrs)

            # MusicXML 中 staff-layout 必须排在 system-layout 前面
            if staves > 1:
                staff_layout = ET.SubElement(
                    print_elem, "staff-layout", {"number": "2"}
                )
                ET.SubElement(staff_layout, "staff-distance").text = "65"

            system_layout = ET.SubElement(print_elem, "system-layout")
            margins = ET.SubElement(system_layout, "system-margins")
            if measure_index == 0:
                ET.SubElement(margins, "left-margin").text = "50"
                ET.SubElement(margins, "right-margin").text = "0"
                ET.SubElement(system_layout, "top-system-distance").text = "170"
            else:
                ET.SubElement(margins, "left-margin").text = "0"
                ET.SubElement(margins, "right-margin").text = "0"
                if line.get("newpage"):
                    ET.SubElement(system_layout, "top-system-distance").text = "120"
                else:
                    ET.SubElement(system_layout, "system-distance").text = "237.5"

        # ---- 调号 / 拍号 ----
        fifths = None
        fifths_data = measure_data.get("fifths")
        if isinstance(fifths_data, dict):
            fifths = _to_int(fifths_data.get("fifths"), 0)
        elif fifths_data is not None:
            fifths = _to_int(fifths_data, 0)

        beats = None
        beat_type = None
        time_data = measure_data.get("time")
        if isinstance(time_data, dict):
            beats = _to_int(time_data.get("beats"), 4)
            beat_type = _to_int(
                time_data.get("beatu", time_data.get("beatType")), 4
            )

        need_attr = (
            measure_index == 0
            or (fifths is not None and fifths != self._last_fifths)
            or (beats is not None and (beats, beat_type) != self._last_time)
            or bool(clef_changes)
        )
        if need_attr:
            attrs = ET.SubElement(measure, "attributes")
            if measure_index == 0:
                ET.SubElement(attrs, "divisions").text = str(DIVISIONS)

            if fifths is not None and fifths != self._last_fifths:
                key = ET.SubElement(attrs, "key")
                ET.SubElement(key, "fifths").text = str(fifths)
                self._last_fifths = fifths

            if beats is not None and (beats, beat_type) != self._last_time:
                time_elem = ET.SubElement(attrs, "time")
                ET.SubElement(time_elem, "beats").text = str(beats)
                ET.SubElement(time_elem, "beat-type").text = str(beat_type)
                self._last_time = (beats, beat_type)

            if measure_index == 0 and staves > 1:
                ET.SubElement(attrs, "staves").text = str(staves)

            for staff_number in sorted(clef_changes):
                clef_name = clef_changes[staff_number]
                sign, clef_line = self._clef_to_sign(clef_name)
                clef = ET.SubElement(
                    attrs, "clef", {"number": str(staff_number)}
                )
                ET.SubElement(clef, "sign").text = sign
                if clef_line is not None:
                    ET.SubElement(clef, "line").text = str(clef_line)
                self._last_clefs[staff_number] = clef_name

        measure_ticks = self._measure_ticks(measure_data, beats, beat_type)

        # 左侧反复/结尾线必须在小节内容之前
        self._add_left_barline(measure, measure_data)

        # ---- 速度标记（只在小节 0） ----
        if measure_index == 0:
            for direction_data in measure_data.get("dirs") or []:
                if direction_data.get("type") != "metronome":
                    continue
                direction = ET.SubElement(measure, "direction", {"placement": "above"})
                direction_type = ET.SubElement(direction, "direction-type")
                metronome = ET.SubElement(
                    direction_type, "metronome", {"parentheses": "no"}
                )
                param = direction_data.get("param") or {}
                metronome.set("default-x", str(param.get("x", 0)))
                metronome.set("relative-y", "20")
                ET.SubElement(metronome, "beat-unit").text = "quarter"
                ET.SubElement(metronome, "per-minute").text = str(
                    direction_data.get("value", "60")
                )
                ET.SubElement(direction, "staff").text = str(
                    direction_data.get("staff", 1)
                )
                ET.SubElement(direction, "sound", {
                    "tempo": str(direction_data.get("value", "60"))
                })

        # ---- 音符 ----
        self._add_notes(measure, notes, measure_ticks, staves)

        # 右侧反复/结尾线在小节内容之后
        self._add_right_barline(measure, measure_data)

    def _add_left_barline(self, measure, measure_data):
        left_bar = measure_data.get("lbar")
        if isinstance(left_bar, dict) and left_bar.get("repeat"):
            barline = ET.SubElement(measure, "barline", {"location": "left"})
            ET.SubElement(barline, "bar-style").text = "heavy-light"
            ET.SubElement(barline, "repeat", {"direction": left_bar["repeat"]})

        endings = measure_data.get("ends")
        if isinstance(endings, dict) and endings:
            barline = ET.SubElement(measure, "barline", {"location": "left"})
            ET.SubElement(barline, "ending", {
                "number": str(endings.get("num", "1")),
                "type": "start",
            })

    def _add_right_barline(self, measure, measure_data):
        endings = measure_data.get("ends")
        if isinstance(endings, dict) and endings.get("stop"):
            barline = ET.SubElement(measure, "barline", {"location": "right"})
            ET.SubElement(barline, "bar-style").text = "light-heavy"
            ET.SubElement(barline, "ending", {
                "number": str(endings.get("num", "1")),
                "type": "stop",
            })
            right_bar = measure_data.get("rbar")
            if isinstance(right_bar, dict) and right_bar.get("repeat"):
                ET.SubElement(barline, "repeat", {
                    "direction": right_bar["repeat"],
                })
            return

        right_bar = measure_data.get("rbar")
        if isinstance(right_bar, dict) and right_bar.get("repeat"):
            barline = ET.SubElement(measure, "barline", {"location": "right"})
            ET.SubElement(barline, "bar-style").text = "light-heavy"
            ET.SubElement(barline, "repeat", {
                "direction": right_bar["repeat"],
            })

    # ------------------------------------------------------------------
    # 音符与声部
    # ------------------------------------------------------------------
    def _add_notes(self, measure, notes, measure_ticks, staves):
        if not notes:
            note = ET.SubElement(measure, "note")
            ET.SubElement(note, "rest", {"measure": "yes"})
            ET.SubElement(note, "duration").text = str(measure_ticks)
            ET.SubElement(note, "voice").text = "1"
            ET.SubElement(note, "type").text = "whole"
            ET.SubElement(note, "staff").text = "1"
            return

        by_staff = defaultdict(list)
        for note_data in notes:
            staff = max(1, _to_int(note_data.get("staff"), 1))
            by_staff[staff].append(note_data)

        voices = []
        for staff in sorted(by_staff):
            for voice_index, voice_notes in enumerate(
                self._assign_voices(by_staff[staff])
            ):
                voices.append((staff, voice_index, voice_notes))

        for index, (staff, voice_index, voice_notes) in enumerate(voices):
            voice_number = (staff - 1) * 4 + min(voice_index, 3) + 1
            cursor = 0

            for note_data in voice_notes:
                tick = max(0, _to_int(note_data.get("tick"), 0))
                tick = max(cursor, min(tick, measure_ticks))
                duration = min(
                    self._duration(note_data),
                    max(1, measure_ticks - tick),
                )

                if tick > cursor:
                    self._add_forward(measure, tick - cursor, voice_number, staff)
                self._add_single_note(
                    measure, note_data, voice_number, staff, duration
                )
                cursor = max(cursor, tick + duration)

            if cursor < measure_ticks:
                self._add_forward(
                    measure, measure_ticks - cursor, voice_number, staff
                )

            # 除了最后一个声部，其余声部结束后回到小节开头
            if index != len(voices) - 1:
                backup = ET.SubElement(measure, "backup")
                ET.SubElement(backup, "duration").text = str(measure_ticks)

    def _assign_voices(self, notes):
        """按 tick 把音符分配到尽量少的声部；同 tick 的重叠音会分到不同声部。"""
        ordered = sorted(
            notes,
            key=lambda item: (
                _to_int(item.get("tick"), 0),
                -self._duration(item),
            ),
        )
        voices = []
        voice_ends = []

        for note_data in ordered:
            tick = max(0, _to_int(note_data.get("tick"), 0))
            duration = self._duration(note_data)
            for index, end in enumerate(voice_ends):
                if end <= tick:
                    voices[index].append(note_data)
                    voice_ends[index] = tick + duration
                    break
            else:
                voices.append([note_data])
                voice_ends.append(tick + duration)

        return voices

    def _add_forward(self, measure, duration, voice, staff):
        forward = ET.SubElement(measure, "forward")
        ET.SubElement(forward, "duration").text = str(max(1, int(duration)))
        ET.SubElement(forward, "voice").text = str(voice)
        ET.SubElement(forward, "staff").text = str(staff)

    def _add_single_note(self, measure, note_data, voice, staff, duration):
        elements = note_data.get("elems") or []
        is_rest = bool(note_data.get("rest")) or not elements

        if is_rest:
            note = ET.SubElement(measure, "note")
            ET.SubElement(note, "rest")
            self._fill_note(note, note_data, voice, staff, duration, lyrics=True)
            return

        for index, element in enumerate(elements):
            note = ET.SubElement(measure, "note")
            if index > 0:
                ET.SubElement(note, "chord")

            step = _to_int(element.get("step"), 1)
            step_name = self.step_map[step - 1] if 1 <= step <= 7 else "C"
            octave = _to_int(element.get("octave"), 4)
            alter = element.get("alter")

            pitch = ET.SubElement(note, "pitch")
            ET.SubElement(pitch, "step").text = step_name
            if alter not in (None, 0, "0", False):
                ET.SubElement(pitch, "alter").text = str(_to_int(alter, 0))
            ET.SubElement(pitch, "octave").text = str(octave)

            self._fill_note(
                note, note_data, voice, staff, duration, lyrics=(index == 0)
            )

    def _fill_note(self, note, note_data, voice, staff, duration, lyrics=False):
        ET.SubElement(note, "duration").text = str(max(1, int(duration)))
        ET.SubElement(note, "voice").text = str(voice)

        note_type = _to_int(note_data.get("type"), 4)
        ET.SubElement(note, "type").text = self.type_map.get(note_type, "quarter")

        for _ in range(max(0, _to_int(note_data.get("dots"), 0))):
            ET.SubElement(note, "dot")

        stem = note_data.get("stem") or {}
        if isinstance(stem, dict) and stem.get("type") in ("up", "down"):
            ET.SubElement(note, "stem").text = stem["type"]

        ET.SubElement(note, "staff").text = str(staff)

        if not lyrics:
            return

        lyric_font = (
            (self.ccxml.get("defaults") or {}).get("lyricfont", "SimHei")
        )
        for lyric_data in note_data.get("lyrics") or []:
            number = _to_int(lyric_data.get("num"), 0) + 1
            lyric = ET.SubElement(note, "lyric", {"number": str(number)})
            ET.SubElement(lyric, "syllabic").text = "single"
            text = ET.SubElement(lyric, "text", {"font-family": lyric_font})
            text.text = str(lyric_data.get("text") or "")

    # ------------------------------------------------------------------
    # 时值工具
    # ------------------------------------------------------------------
    @staticmethod
    def _clef_to_sign(name):
        name = (name or "").strip().lower()
        if name.startswith("bass"):
            return "F", 4
        if name.startswith("alto"):
            return "C", 3
        if name.startswith("tenor"):
            return "C", 4
        if name.startswith("percussion"):
            return "percussion", None
        return "G", 2

    def _duration(self, note_data):
        note_type = _to_int(note_data.get("type"), 4)
        base = TYPE_TICKS.get(note_type, DIVISIONS)
        dots = max(0, _to_int(note_data.get("dots"), 0))
        if dots:
            base = base * (2 - 1 / (2 ** dots))
        return max(1, int(round(base)))

    def _measure_ticks(self, measure_data, beats=None, beat_type=None):
        ticks = _to_int(measure_data.get("ticks"), 0)
        if ticks > 0:
            return ticks
        if beats and beat_type:
            return int(DIVISIONS * beats * 4 / beat_type)
        return DIVISIONS * 4

    def _title(self):
        title = self.ccxml.get("title")
        return title if isinstance(title, dict) else {}

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------
    def _prettify(self, elem):
        rough_string = ET.tostring(elem, encoding="utf-8")
        reparsed = minidom.parseString(rough_string)

        impl = minidom.getDOMImplementation()
        doctype = impl.createDocumentType(
            "score-partwise",
            "-//Recordare//DTD MusicXML 4.0 Partwise//EN",
            "http://www.musicxml.org/dtds/partwise.dtd",
        )

        document = impl.createDocument(None, "score-partwise", doctype)
        document.replaceChild(reparsed.documentElement, document.documentElement)

        return document.toprettyxml(indent="  ", encoding="UTF-8").decode("utf-8")


def convert_ccxml_to_musicxml(ccxml_data, config=None):
    """主转换函数。"""
    return CCXMLToMusicXML(ccxml_data, config).convert()
