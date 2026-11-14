# -*- coding: utf-8 -*-
"""使用 LilyPond 把 MusicXML 渲染成谱面图片 / PDF。

流程：MusicXML -> LY (musicxml2ly) -> PNG/PDF (lilypond)
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import platform


def _page_sort_key(path):
    """按页码排序：score.png、score-1.png、score-2.png……"""
    match = re.search(r"(\d+)(?=\.png$)", os.path.basename(path), re.IGNORECASE)
    return int(match.group(1)) if match else 0


def pngs_to_pdf(png_paths, pdf_path):
    """把多张 PNG 页面合成一个 A4 PDF。"""
    try:
        from PIL import Image
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出提示
        print(f"缺少 PDF 依赖: {exc}")
        return False

    if not png_paths:
        return False

    pdf = canvas.Canvas(str(pdf_path), pagesize=A4)
    page_width, page_height = A4

    for png_path in png_paths:
        with Image.open(png_path) as image:
            image = image.convert("RGB")
            width, height = image.size
            scale = min(page_width / width, page_height / height)
            draw_width = width * scale
            draw_height = height * scale
            pdf.drawImage(
                ImageReader(image),
                (page_width - draw_width) / 2,
                (page_height - draw_height) / 2,
                draw_width,
                draw_height,
            )
        pdf.showPage()

    pdf.save()
    return True


class ScoreGenerator:
    """把 MusicXML 渲染成谱面。"""

    def __init__(self):
        self.system = platform.system()
        self.is_android = self._detect_android()
        self.lilypond_cmd = None
        self.musicxml2ly_script = None
        self.python_exe = None

    def _detect_android(self):
        try:
            return "com.termux" in os.environ.get("PREFIX", "") or os.path.exists(
                "/data/data/com.termux"
            )
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 查找 LilyPond
    # ------------------------------------------------------------------
    def _use_lilypond(self, lilypond_path):
        """记录 lilypond 路径，并找同目录下的 musicxml2ly 和 python。"""
        if not lilypond_path or not os.path.isfile(lilypond_path):
            return False

        self.lilypond_cmd = lilypond_path
        bin_dir = os.path.dirname(lilypond_path)

        for name in ("musicxml2ly.py", "musicxml2ly.exe", "musicxml2ly"):
            candidate = os.path.join(bin_dir, name)
            if os.path.isfile(candidate):
                self.musicxml2ly_script = candidate
                break

        for name in ("python.exe", "python3.exe", "python3", "python"):
            candidate = os.path.join(bin_dir, name)
            if os.path.isfile(candidate):
                self.python_exe = candidate
                break

        return True

    def _find_lilypond_windows(self):
        found = shutil.which("lilypond")
        if found and self._use_lilypond(found):
            return True

        local_appdata = os.environ.get("LOCALAPPDATA", "")
        bases = [
            os.path.join(local_appdata, "lilyponddist"),
            os.path.join(local_appdata, "Programs", "LilyPond"),
            os.path.join(local_appdata, "LilyPond"),
            r"C:\Program Files\LilyPond",
            r"C:\Program Files (x86)\LilyPond",
            r"C:\LilyPond",
        ]

        candidates = []
        for base in bases:
            if not base or not os.path.isdir(base):
                continue
            candidates.append(os.path.join(base, "bin", "lilypond.exe"))
            candidates.append(os.path.join(base, "usr", "bin", "lilypond.exe"))
            candidates.append(
                os.path.join(base, "lilyponddist", "bin", "lilypond.exe")
            )
            try:
                for item in os.listdir(base):
                    item_path = os.path.join(base, item)
                    if os.path.isdir(item_path):
                        candidates.append(
                            os.path.join(item_path, "bin", "lilypond.exe")
                        )
                        candidates.append(
                            os.path.join(item_path, "usr", "bin", "lilypond.exe")
                        )
            except OSError:
                pass

        for candidate in candidates:
            if os.path.isfile(candidate):
                return self._use_lilypond(candidate)
        return False

    def _find_lilypond_unix(self):
        for name in ("lilypond", "lilypond.exe"):
            found = shutil.which(name)
            if found and self._use_lilypond(found):
                return True
        return False

    def setup_lilypond(self):
        print(f"检测操作系统: {self.system}")
        print(f"是否为 Android: {self.is_android}")

        if self.is_android:
            return self._install_lilypond_android()

        found = shutil.which("lilypond")
        if found and self._use_lilypond(found):
            return True

        if self.system == "Windows":
            return self._find_lilypond_windows()
        return self._find_lilypond_unix()

    def _install_lilypond_android(self):
        print("检测到 Android 环境 (Termux)，尝试安装 LilyPond...")
        try:
            subprocess.run(["pkg", "update", "-y"], check=True)
            subprocess.run(["pkg", "install", "lilypond", "-y"], check=True)
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"安装 LilyPond 时出错: {exc}")
            return False

        found = shutil.which("lilypond")
        if found:
            print(f"LilyPond 安装成功: {found}")
            return self._use_lilypond(found)

        print("LilyPond 安装失败")
        return False

    # ------------------------------------------------------------------
    # 转换
    # ------------------------------------------------------------------
    def musicxml_to_ly(self, musicxml_path, ly_output_path):
        if not self.lilypond_cmd:
            print("错误: 未找到 LilyPond")
            return False

        script = self.musicxml2ly_script
        if not script:
            bin_dir = os.path.dirname(self.lilypond_cmd)
            for name in ("musicxml2ly.py", "musicxml2ly.exe", "musicxml2ly"):
                candidate = os.path.join(bin_dir, name)
                if os.path.isfile(candidate):
                    script = candidate
                    break

        if not script:
            print("错误: 找不到 musicxml2ly（LilyPond 安装不完整？）")
            return False

        # musicxml2ly 对反复记号的处理有 bug，先去掉 <repeat>，保留小节线。
        cleaned_path = musicxml_path + ".clean.xml"
        try:
            with open(musicxml_path, "r", encoding="utf-8") as file:
                xml = file.read()
            xml = re.sub(
                r"<barline[^>]*>[\s\S]*?<repeat[^/]*/?>[\s\S]*?</barline>",
                "",
                xml,
            )
            with open(cleaned_path, "w", encoding="utf-8") as file:
                file.write(xml)
        except OSError as exc:
            print(f"读取/预处理 MusicXML 失败: {exc}")
            return False

        interpreter = self.python_exe or sys.executable
        is_python_script = script.lower().endswith(".py")
        if os.name == "nt" and not script.lower().endswith(".exe"):
            # Windows 不能直接执行无扩展名的 Python 脚本（musicxml2ly）
            is_python_script = True

        if is_python_script:
            command = [interpreter, script]
        else:
            command = [script]
        command.extend(["-o", ly_output_path, cleaned_path])

        print("使用 LilyPond 内置 musicxml2ly 转换 MusicXML -> LY...")
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            print("musicxml2ly 超时（超过 180 秒）")
            return False
        except OSError as exc:
            print(f"无法运行 musicxml2ly: {exc}")
            return False
        finally:
            try:
                os.remove(cleaned_path)
            except OSError:
                pass

        if result.returncode != 0:
            print("musicxml2ly 返回错误:")
            print((result.stderr or result.stdout or "").strip()[:4000])
            return False

        if os.path.exists(ly_output_path):
            return True
        # 少数版本会自动补 .ly
        if os.path.exists(ly_output_path + ".ly"):
            os.replace(ly_output_path + ".ly", ly_output_path)
            return True

        print(f"转换失败: {ly_output_path} 未生成")
        return False

    def ly_to_png(self, ly_path, output_basename):
        """把 .ly 渲染成 PNG。output_basename 不带扩展名，输出到 ly 所在目录。"""
        if not self.lilypond_cmd:
            print("错误: 未找到 LilyPond")
            return False

        work_dir = os.path.dirname(ly_path) or "."
        output_name = os.path.basename(output_basename)

        print(f"使用 LilyPond: {self.lilypond_cmd}")
        print(f"渲染 LY: {os.path.basename(ly_path)}")

        command = [
            self.lilypond_cmd,
            "--png",
            "-dresolution=300",
            "-dno-point-and-click",
            f"-doutput={output_name}",
            os.path.basename(ly_path),
        ]

        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                cwd=work_dir,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            print("LilyPond 渲染超时（超过 300 秒）")
            return False
        except OSError as exc:
            print(f"无法运行 LilyPond: {exc}")
            return False

        if result.returncode != 0:
            error = (result.stderr or b"").decode("utf-8", errors="replace")
            print("LilyPond 错误:")
            print(error.strip()[:4000])
            return False

        print("LilyPond 渲染成功")
        return True

    def generate_score_from_musicxml(
        self,
        musicxml_content,
        output_dir,
        base_name,
        want_png=True,
        want_pdf=False,
    ):
        """生成谱面。

        返回 {'png': [...], 'pdf': path_or_None}。
        """
        result = {"png": [], "pdf": None}
        os.makedirs(output_dir, exist_ok=True)

        with tempfile.TemporaryDirectory() as temp_dir:
            # 临时文件固定用 ASCII 名，避免 LilyPond/Guile 处理中文路径出问题
            musicxml_path = os.path.join(temp_dir, "score.musicxml")
            ly_path = os.path.join(temp_dir, "score.ly")
            output_basename = os.path.join(temp_dir, "score")

            try:
                with open(musicxml_path, "w", encoding="utf-8") as file:
                    file.write(musicxml_content)
            except OSError as exc:
                print(f"写入临时 MusicXML 失败: {exc}")
                return result

            if not self.musicxml_to_ly(musicxml_path, ly_path):
                print("MusicXML 转 LY 失败")
                return result

            if not self.ly_to_png(ly_path, output_basename):
                print("LY 转 PNG 失败")
                return result

            pages = sorted(
                (
                    os.path.join(temp_dir, name)
                    for name in os.listdir(temp_dir)
                    if name.lower().endswith(".png")
                ),
                key=_page_sort_key,
            )
            if not pages:
                print("LilyPond 没有生成 PNG")
                return result

            if want_png:
                for index, page in enumerate(pages, 1):
                    destination = os.path.join(
                        output_dir, f"{base_name}-page{index}.png"
                    )
                    shutil.copy2(page, destination)
                    result["png"].append(destination)

            if want_pdf:
                pdf_path = os.path.join(output_dir, f"{base_name}.pdf")
                if pngs_to_pdf(pages, pdf_path):
                    result["pdf"] = pdf_path

            print(f"生成了 {len(pages)} 页谱面")
            return result


def check_environment():
    """检查并打印 LilyPond 环境。"""
    generator = ScoreGenerator()

    print("=" * 50)
    print("环境检测")
    print("=" * 50)

    if not generator.setup_lilypond():
        print("✗ LilyPond 未找到")
        print("\n请安装 LilyPond:")
        if generator.is_android:
            print("  pkg install lilypond")
        elif generator.system == "Windows":
            print("  访问 https://lilypond.org/download.html")
            print("  或放到 %LOCALAPPDATA%\\lilyponddist\\bin\\lilypond.exe")
        else:
            print("  sudo apt-get install lilypond  (Debian/Ubuntu)")
            print("  sudo yum install lilypond      (CentOS/RHEL)")
            print("  brew install lilypond          (macOS)")
        return False

    print("✓ LilyPond 已找到")
    print(f"  路径: {generator.lilypond_cmd}")

    if generator.musicxml2ly_script:
        print("✓ musicxml2ly 已找到")
        print(f"  路径: {generator.musicxml2ly_script}")
    else:
        print("⚠ musicxml2ly 未找到")

    if generator.python_exe:
        print("✓ LilyPond Python 已找到")
        print(f"  路径: {generator.python_exe}")

    return True


if __name__ == "__main__":
    check_environment()
