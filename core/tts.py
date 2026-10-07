"""P1 · 单词发音引擎与缓存（`PRODUCT_SPEC` 12.2 F3）。

设计原则：
  * **Google 联网发音优先 + 本地持久化缓存 + 离线语音无损兜底**：
    - 在 `online` 模式下，首次朗读一个词条时后台通过 Google Translate TTS 接口拉取韩语发音，
      并以原子写入方式缓存在 `data/tts/`。
    - 同一个词第二次朗读直接读本地缓存 MP3，**零网络请求、零延迟**。
    - 断网且本地尚未缓存该词时，自动降级到系统本地韩语语音（`QTextToSpeech`），
      绝不阻塞过词或损坏已有数据（`PRODUCT_SPEC` 8.6 / 12.5）。
  * **文本清洗（`clean_korean_for_tts`）**：
    - 词表中常见的语法条目（如 `-(으)니까`、`-(으)ㄹ까`、`~기 마련이다`）或带括号中文注释、
      斜杠并列项，先清洗为自然连读的韩语文本，避免 TTS 读出"波浪号、横杠、括号"等符号名。
"""

import hashlib
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

from PySide6.QtCore import QLocale, QObject, QThread, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

from core.config import DEFAULT_TTS_VOICE, TTS_DIR

TTS_VOICES = (
    ("google_ko", "Google 韩语标准发音（默认）"),
    ("google_ko_slow", "Google 韩语慢速清晰发音"),
)

# 语法条目里常见的 "(으) + 韵尾辅音" 组合，合并为自然发音音节
_GRAMMAR_COLLAPSE = (
    (re.compile(r"\(으\)\s*ㄹ"), "을"),
    (re.compile(r"\(으\)\s*ㄴ"), "은"),
    (re.compile(r"\(으\)\s*ㅁ"), "음"),
    (re.compile(r"\(으\)\s*ㅂ"), "읍"),
)

# 仅包含 1~2 个韩文音节的括号（如 (으)、(이)、(가)、(를)、(시)），保留括号内音节自然连读
_PURE_HANGUL_PAREN = re.compile(r"[\(\uff08]([가-힣]{1,2})[\)\uff09]")

# 其它括号注释（如中文说明、词性标注、英文备注等），整体剔除
_OTHER_BRACKETS = re.compile(r"(\([^)]*\)|（[^）]*）|\[[^\]]*\]|【[^】]*】)")

# 语法连接符号与占位符
_STRIP_SYMBOLS = re.compile(r"[-~∼―·*…_+=<>]+")

# 并列分隔符替换为逗号停顿
_PAUSE_SEPARATORS = re.compile(r"\s*[/／、;；|]\s*")

# 孤立的韩文谚文子音/母音符号（如 N-기 / V-ㄴ다 里的 ㄴ、ㄹ），剔除以免读出字母名
_STANDALONE_JAMO = re.compile(r"[ㄱ-ㅎㅏ-ㅣ]+")

# 仅保留韩文音节、空格与基本停顿标点
_NON_KOREAN_CLEAN = re.compile(r"[^가-힣\s,.?!]")


def clean_korean_for_tts(text: str) -> str:
    """将词表中的韩语词条清洗为适合 TTS 自然朗读的纯韩语文本。"""
    s = (text or "").strip()
    if not s:
        return ""

    for pattern, replacement in _GRAMMAR_COLLAPSE:
        s = pattern.sub(replacement, s)

    s = _PURE_HANGUL_PAREN.sub(r"\1", s)
    s = _OTHER_BRACKETS.sub(" ", s)
    s = _PAUSE_SEPARATORS.sub(", ", s)
    s = _STRIP_SYMBOLS.sub(" ", s)
    s = _STANDALONE_JAMO.sub("", s)
    s = _NON_KOREAN_CLEAN.sub(" ", s)
    s = re.sub(r"(?:\s*,\s*)+", ", ", s)
    s = re.sub(r"\s+", " ", s).strip(" ,.")
    return s


def cache_path_for(cleaned_text: str, voice: str = DEFAULT_TTS_VOICE) -> Path:
    """返回指定清洗后文本与模式在 `data/tts/` 下的缓存文件路径。"""
    voice_key = (voice or DEFAULT_TTS_VOICE).strip()
    raw_key = f"{voice_key}:{cleaned_text}".encode("utf-8")
    digest = hashlib.sha256(raw_key).hexdigest()[:24]
    return TTS_DIR / f"{digest}.mp3"


def is_cached(cleaned_text: str, voice: str = DEFAULT_TTS_VOICE) -> bool:
    """检查目标发音是否已有有效本地缓存。"""
    if not cleaned_text:
        return False
    path = cache_path_for(cleaned_text, voice)
    try:
        return path.is_file() and path.stat().st_size > 256
    except OSError:
        return False


def clear_tts_cache() -> int:
    """清理 `data/tts/` 下的所有发音缓存文件，返回删除的文件数。"""
    if not TTS_DIR.is_dir():
        return 0
    removed = 0
    for entry in TTS_DIR.iterdir():
        if entry.is_file() and entry.suffix.lower() in (".mp3", ".tmp"):
            try:
                entry.unlink()
                removed += 1
            except OSError:
                continue
    return removed


class _TTSFetchWorker(QThread):
    """后台拉取 Google TTS 韩语音频并原子写入本地缓存目录。"""

    completed = Signal(int, str, str, str)  # request_id, cleaned_text, file_path, error

    def __init__(self, request_id: int, cleaned_text: str, voice: str, target_path: Path):
        super().__init__()
        self.request_id = request_id
        self.cleaned_text = cleaned_text
        self.voice = voice or DEFAULT_TTS_VOICE
        self.target_path = Path(target_path)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            return
        try:
            TTS_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.completed.emit(self.request_id, self.cleaned_text, "", str(exc))
            return

        tmp_path = self.target_path.with_suffix(f".{self.request_id}.tmp")
        try:
            ok = self._fetch_google_tts(tmp_path)

            if self._cancelled:
                self._cleanup_tmp(tmp_path)
                return

            if ok and tmp_path.is_file() and tmp_path.stat().st_size > 256:
                os.replace(tmp_path, self.target_path)
                self.completed.emit(
                    self.request_id, self.cleaned_text, str(self.target_path), ""
                )
                return

            self._cleanup_tmp(tmp_path)
            self.completed.emit(
                self.request_id,
                self.cleaned_text,
                "",
                "Google 语音服务未返回有效音频",
            )
        except Exception as exc:
            self._cleanup_tmp(tmp_path)
            if not self._cancelled:
                self.completed.emit(self.request_id, self.cleaned_text, "", str(exc))

    def _fetch_google_tts(self, tmp_path: Path) -> bool:
        """从 Google Translate TTS 拉取韩语 MP3（支持标准语速与慢速跟读）。"""
        query = urllib.parse.quote(self.cleaned_text)
        speed = "0.24" if self.voice == "google_ko_slow" else "1"
        endpoints = (
            f"https://translate.googleapis.com/translate_tts?ie=UTF-8&client=tw-ob&tl=ko&ttsspeed={speed}&q={query}",
            f"https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob&tl=ko&ttsspeed={speed}&q={query}",
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }
        for url in endpoints:
            if self._cancelled:
                return False
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = resp.read()
                if data and len(data) > 256:
                    tmp_path.write_bytes(data)
                    return True
            except Exception:
                self._cleanup_tmp(tmp_path)
                continue
        return False

    @staticmethod
    def _cleanup_tmp(tmp_path: Path):
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


class WordSpeaker(QObject):
    """单词发音控制器：统一调度本地缓存播放、后台 Google TTS 拉取与系统离线语音兜底。"""

    # level ("info" | "warning" | "danger"), message
    notice = Signal(str, str)

    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.database = database
        self._request_seq = 0
        self._workers = set()
        self._offline_warned = False

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(1.0)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.refresh_audio_device()

        self._local_tts = None
        self._local_korean_ready = None

    def refresh_audio_device(self):
        """同步 P5 设置页选定的输出设备（与 P2 影子跟读保持一致）。"""
        wanted = self.database.get_audio_device("output")
        chosen = QMediaDevices.defaultAudioOutput()
        if wanted:
            for dev in QMediaDevices.audioOutputs():
                if dev.description() == wanted:
                    chosen = dev
                    break
        self.audio_output.setDevice(chosen)

    def speak(self, raw_korean: str) -> bool:
        """朗读给定韩语词条。返回是否成功发起朗读。"""
        cleaned = clean_korean_for_tts(raw_korean)
        if not cleaned:
            return False

        self._request_seq += 1
        req_id = self._request_seq

        mode = self.database.get_tts_mode()
        voice = self.database.get_tts_voice()

        if mode == "local":
            self.player.stop()
            return self._speak_local(cleaned, explicit_local=True)

        # online 模式：先查本地缓存，命中则零延迟直接播放
        cached_file = cache_path_for(cleaned, voice)
        try:
            if cached_file.is_file() and cached_file.stat().st_size > 256:
                self._play_file(cached_file)
                return True
        except OSError:
            pass

        # 未命中缓存：后台拉取，完成后若仍是最新请求则立即播放
        self._start_fetch(req_id, cleaned, voice, cached_file)
        return True

    def stop(self):
        """停止当前正在播放的语音。"""
        self._request_seq += 1
        self.player.stop()
        if self._local_tts is not None:
            try:
                self._local_tts.stop()
            except Exception:
                pass

    def _play_file(self, path: Path):
        if self._local_tts is not None:
            try:
                self._local_tts.stop()
            except Exception:
                pass
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def _start_fetch(self, req_id: int, cleaned: str, voice: str, target_path: Path):
        for w in list(self._workers):
            if w.isRunning():
                w.cancel()

        worker = _TTSFetchWorker(req_id, cleaned, voice, target_path)
        self._workers.add(worker)
        worker.completed.connect(self._on_fetch_completed)
        worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
        worker.start()

    def _cleanup_worker(self, worker: _TTSFetchWorker):
        self._workers.discard(worker)
        worker.deleteLater()

    def _on_fetch_completed(self, req_id: int, cleaned: str, file_path: str, error: str):
        # 如果用户已经切到了下一个词，只静默保留缓存文件，不再插播旧词
        if req_id != self._request_seq:
            return

        if file_path and os.path.isfile(file_path):
            self._offline_warned = False
            self._play_file(Path(file_path))
            return

        # 联网合成失败：自动退回系统本地韩语语音（PRODUCT_SPEC 8.6 / 12.2 F3）
        ok_local = self._speak_local(cleaned, explicit_local=False)
        if not self._offline_warned:
            self._offline_warned = True
            if ok_local:
                self.notice.emit("warning", "当前无法连接 Google 语音服务，已切换为系统离线韩语发音")
            else:
                self.notice.emit(
                    "warning",
                    "当前无法连接 Google 语音服务，且系统未安装离线韩语语音包",
                )

    def _ensure_local_tts(self) -> bool:
        """按需初始化系统本地 `QTextToSpeech` 并定位韩语语音包。"""
        if self._local_korean_ready is not None:
            return self._local_korean_ready

        try:
            from PySide6.QtTextToSpeech import QTextToSpeech

            tts = QTextToSpeech(self)
            for locale in tts.availableLocales():
                if (
                    locale.language() == QLocale.Language.Korean
                    or locale.name().lower().startswith("ko")
                ):
                    tts.setLocale(locale)
                    for v in tts.availableVoices():
                        if v.locale().language() == QLocale.Language.Korean:
                            tts.setVoice(v)
                            break
                    self._local_tts = tts
                    self._local_korean_ready = True
                    return True
        except Exception:
            pass

        self._local_korean_ready = False
        return False

    def _speak_local(self, cleaned: str, explicit_local: bool = False) -> bool:
        if not self._ensure_local_tts() or self._local_tts is None:
            if explicit_local:
                self.notice.emit(
                    "warning",
                    "系统未安装 Windows 韩语语音包。可在「设置与数据」中切换为 Google 联网语音，或在系统中安装韩语语音。",
                )
            return False
        try:
            self._local_tts.stop()
            self._local_tts.say(cleaned)
            return True
        except Exception:
            return False

    def shutdown(self):
        """窗口关闭时停止播放并等待后台请求线程安全退出。"""
        self._request_seq += 1
        self.stop()
        for worker in list(self._workers):
            worker.cancel()
            worker.wait(1500)
        self._workers.clear()
