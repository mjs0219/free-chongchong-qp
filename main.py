#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从本地 .ccmz 文件导出 MIDI / MusicXML / 谱面 PNG / PDF。

不再联网下载，直接读取你已有的 .ccmz 文件。

示例：
    python main.py song.ccmz
    python main.py song.ccmz -t midi,musicxml
    python main.py song.ccmz -t png -o output
    python main.py song.ccmz -t all
    python main.py --check-env
"""

import argparse
import json
import sys
import zipfile
from pathlib import Path

from ccmz import LibCCMZ
from ccxml_to_musicxml import convert_ccxml_to_musicxml
from score_generator import ScoreGenerator, check_environment

ALL_TYPES = {"midi", "musicxml", "png", "pdf"}
SCORE_TYPES = {"musicxml", "png", "pdf"}


def _parse_types(value):
    """支持 midi / musicxml / png / pdf / all，也支持逗号组合。"""
    value = (value or "midi").strip().lower()
    if value == "all":
        return set(ALL_TYPES)

    result = {part.strip() for part in value.split(",") if part.strip()}
    invalid = result - ALL_TYPES
    if invalid:
        raise argparse.ArgumentTypeError(
            "不支持的导出类型: "
            + ", ".join(sorted(invalid))
            + "；可选: midi, musicxml, png, pdf, all"
        )
    return result


def safe_base_name(name):
    cleaned = "".join(
        char if char not in '/\\:*?"<>|' else "_" for char in name
    ).strip()
    return cleaned or "score"


def _build_parser():
    parser = argparse.ArgumentParser(
        description="从本地 .ccmz 文件导出 MIDI / MusicXML / 谱面 PNG / PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python main.py song.ccmz
  python main.py song.ccmz -t midi,musicxml
  python main.py song.ccmz -t png -o output
  python main.py song.ccmz -t all
  python main.py --check-env
""",
    )
    parser.add_argument("source", nargs="?", help="本地 .ccmz 文件路径")
    parser.add_argument(
        "-t", "--type",
        default="midi",
        type=_parse_types,
        metavar="TYPE",
        help="导出类型: midi, musicxml, png, pdf, all（可逗号组合，默认 midi）",
    )
    parser.add_argument(
        "-o", "--output",
        help="输出目录（默认与 .ccmz 文件同目录）",
    )
    parser.add_argument(
        "--name",
        help="输出文件名（不含扩展名，默认与 .ccmz 同名）",
    )
    parser.add_argument(
        "--ppq",
        type=int,
        help="覆盖 MIDI 的 PPQ（默认从文件识别，回退 480）",
    )
    parser.add_argument(
        "--check-env",
        action="store_true",
        help="检查 LilyPond 环境后退出",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="只输出错误信息")
    return parser


def _export_midi(info, output_dir, base_name, ppq, quiet=False):
    path = output_dir / f"{base_name}.mid"

    if info.version == 1:
        path.write_bytes(info.midi)
        if not quiet:
            print(f"MIDI: {path}")
        return path

    data = json.loads(info.midi)
    stats = {}
    LibCCMZ.write_midi(data, path, ppq=ppq, stats=stats)

    if not quiet:
        print(
            f"MIDI: {path}  "
            f"(PPQ {stats['ppq']}, {stats['notes']} 个音符, "
            f"{stats['tracks']} 条轨道)"
        )
        if stats["unterminated"]:
            print(
                f"  提示: {stats['unterminated']} 个 noteOn 没有 noteOff，"
                "已按自带 duration 或默认半拍处理"
            )
    return path


def _build_musicxml(info):
    if info.version != 2:
        raise ValueError("MusicXML/谱面导出只支持 ccmz v2（v1 只有 data.xml）")
    score_data = json.loads(info.score)
    return convert_ccxml_to_musicxml(score_data)


def _export_score(musicxml_content, output_dir, base_name, types, quiet=False):
    generator = ScoreGenerator()
    if not generator.setup_lilypond():
        print("未找到 LilyPond，跳过 PNG/PDF 导出。")
        print("可运行 python main.py --check-env 查看环境，或先安装 LilyPond。")
        return False

    try:
        result = generator.generate_score_from_musicxml(
            musicxml_content,
            str(output_dir),
            base_name,
            want_png="png" in types,
            want_pdf="pdf" in types,
        )
    except Exception as exc:  # LilyPond / 文件系统错误统一给出提示
        print(f"谱面生成失败: {exc}")
        return False

    if not quiet:
        for path in result["png"]:
            print(f"PNG: {path}")
        if result["pdf"]:
            print(f"PDF: {result['pdf']}")

    success = bool(result["png"] or result["pdf"])
    if not success:
        print("谱面生成失败（未产生 PNG/PDF）。")
    return success


def export_ccmz(info, types, output_dir, base_name, ppq=None, quiet=False):
    """把已解析的 ccmz 导出为指定格式，返回是否全部成功。

    types 为 {"midi", "musicxml", "png", "pdf"} 的子集。
    """
    types = set(types)
    output_dir = Path(output_dir)
    score_types = types & SCORE_TYPES

    if score_types and info.version != 2:
        print("提示: ccmz v1 没有 score.json，跳过 MusicXML/谱面导出。")
        types -= score_types
        score_types = set()
        if not types:
            return False

    output_dir.mkdir(parents=True, exist_ok=True)

    if not quiet:
        print(f"版本: v{info.version}")
        print(f"导出类型: {', '.join(sorted(types))}")
        print(f"输出目录: {output_dir}")

    if "midi" in types:
        _export_midi(info, output_dir, base_name, ppq, quiet)

    score_ok = True
    if score_types:
        musicxml_content = _build_musicxml(info)

        if "musicxml" in types:
            path = output_dir / f"{base_name}.musicxml"
            path.write_text(musicxml_content, encoding="utf-8")
            if not quiet:
                print(f"MusicXML: {path}")

        if types & {"png", "pdf"}:
            score_ok = _export_score(
                musicxml_content, output_dir, base_name, types, quiet
            )

    return score_ok


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.check_env:
        return 0 if check_environment() else 1

    if not args.source:
        parser.error("请提供 .ccmz 文件路径（或用 --check-env 检查环境）")

    source = Path(args.source).expanduser()
    if not source.is_file():
        print(f"错误: 文件不存在: {source}", file=sys.stderr)
        return 1

    try:
        info = LibCCMZ.read_ccmz(source.read_bytes())
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"错误: 读取 ccmz 失败: {exc}", file=sys.stderr)
        return 1

    output_dir = Path(args.output).expanduser() if args.output else source.parent
    base_name = safe_base_name(args.name or source.stem)

    if not args.quiet:
        print(f"文件: {source}")

    try:
        score_ok = export_ccmz(
            info, args.type, output_dir, base_name, args.ppq, args.quiet
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    return 0 if score_ok else 1


if __name__ == "__main__":
    sys.exit(main())
