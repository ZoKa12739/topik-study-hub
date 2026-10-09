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
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from PySide6.QtCore import QLocale, QObject, QThread, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

from core.config import DEFAULT_AZURE_REGION, DEFAULT_TTS_VOICE, TTS_DIR

TTS_VOICES = (
    ("azure_ko_dragon_hd", "Azure SunHi Dragon HD Latest（高清自然，推荐）"),
    ("azure_ko_dragon_hd_slow", "Azure SunHi Dragon HD Latest（慢速精读 0.9x）"),
    ("azure_ko_sunhi_standard", "Azure SunHi 标准女声 (ko-KR-SunHiNeural)"),
    ("google_ko", "Google 韩语标准发音"),
    ("google_ko_slow", "Google 韩语慢速清晰发音"),
)

_AZURE_VOICE_SPECS = {
    "azure_ko_dragon_hd": ("ko-KR-SunHi:DragonHDLatestNeural", "1.0"),
    "azure_ko_dragon_hd_slow": ("ko-KR-SunHi:DragonHDLatestNeural", "0.9"),
    "azure_ko_sunhi_standard": ("ko-KR-SunHiNeural", "1.0"),
}

_BASE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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

# 孤立的韩文谚文子音/母音符号（如 N-기 / V-ㄴ达 里的 ㄴ、ㄹ），剔除以免读出字母名
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


def _get_effective_proxies() -> dict[str, str]:
    """合并系统注册表代理与环境变量代理。

    Python 标准库 `urllib.request.getproxies()` 在环境变量中存在 `NO_PROXY` 时，
    会直接忽略 Windows 注册表里的系统代理（如 `127.0.0.1:7897`）。这里显式合并两者。
    """
    proxies: dict[str, str] = {}
    get_reg = getattr(urllib.request, "getproxies_registry", None)
    if callable(get_reg):
        try:
            proxies.update(get_reg() or {})
        except Exception:
            pass
    try:
        env_proxies = urllib.request.getproxies_environment() or {}
        for k, v in env_proxies.items():
            if k in ("http", "https") and v:
                proxies[k] = v
            elif k == "no" and "no" not in proxies:
                proxies[k] = v
    except Exception:
        pass
    return proxies


def _build_url_opener() -> urllib.request.OpenerDirector:
    proxies = _get_effective_proxies()
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies))


def synthesize_azure(
    cleaned_text: str,
    voice: str = "azure_ko_dragon_hd",
    key: str = "",
    region: str = "",
    timeout: float = 8.0,
) -> bytes:
    """通过 Azure AI Speech REST API 合成高保真韩语音频字节流。

    密钥来源只有两个：调用方传入（用户在 P5 填的那个）或 `AZURE_SPEECH_KEY`
    环境变量——BYOK，工具侧不持有任何默认密钥（PRODUCT_SPEC 12.5）。
    """
    active_key = (key or os.environ.get("AZURE_SPEECH_KEY") or "").strip()
    active_region = (region or os.environ.get("AZURE_SPEECH_REGION") or DEFAULT_AZURE_REGION).strip()
    if not active_key:
        raise RuntimeError("未配置 Azure Speech Key")

    voice_name, rate = _AZURE_VOICE_SPECS.get(voice, ("ko-KR-SunHi:DragonHDLatestNeural", "1.0"))

    speak = ET.Element(
        "speak",
        {
            "version": "1.0",
            "xmlns": "http://www.w3.org/2001/10/synthesis",
            "xml:lang": "ko-KR",
        },
    )
    voice_node = ET.SubElement(speak, "voice", {"name": voice_name})
    prosody = ET.SubElement(voice_node, "prosody", {"rate": rate})
    prosody.text = cleaned_text
    ssml = ET.tostring(speak, encoding="utf-8")

    req = urllib.request.Request(
        f"https://{active_region}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ssml,
        headers={
            "Ocp-Apim-Subscription-Key": active_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-16khz-128kbitrate-mono-mp3",
            "User-Agent": "TOPIKStudyHub/1.0",
        },
        method="POST",
    )

    opener = _build_url_opener()
    with opener.open(req, timeout=timeout) as resp:
        audio = resp.read()

    if not audio or len(audio) < 256:
        raise RuntimeError("Azure AI Speech 未返回有效音频")
    return audio


def probe_azure_tts(
    key: str = "",
    region: str = "",
    voice: str = "azure_ko_dragon_hd",
    timeout: float = 5.0,
) -> tuple[bool, str]:
    """实测 Azure AI Speech 的网络连通性与响应延迟。密钥同样仅来自参数或环境变量。"""
    active_key = (key or os.environ.get("AZURE_SPEECH_KEY") or "").strip()
    active_region = (region or os.environ.get("AZURE_SPEECH_REGION") or DEFAULT_AZURE_REGION).strip()
    if not active_key:
        return False, "未配置 Azure Speech Key"

    proxies = _get_effective_proxies()
    proxy_url = proxies.get("https") or proxies.get("http") or ""
    proxy_desc = f"系统代理 {proxy_url}" if proxy_url else "直连/TUN"

    t0 = time.perf_counter()
    try:
        audio = synthesize_azure(
            "안녕하세요", voice=voice, key=active_key, region=active_region, timeout=timeout
        )
        elapsed_ms = max(1, round((time.perf_counter() - t0) * 1000))
        if audio and len(audio) > 256:
            return (
                True,
                f"Azure 语音服务连接正常（延迟 {elapsed_ms} ms · {active_region} · {proxy_desc}）",
            )
        return False, "Azure 语音服务返回音频为空"
    except Exception as exc:
        reason = getattr(exc, "reason", None) or exc
        err_msg = str(reason)
        if "401" in err_msg or "Unauthorized" in err_msg:
            err_msg = "密钥无效 (401 Unauthorized)"
        elif "timed out" in err_msg.lower() or "10060" in err_msg:
            err_msg = "连接超时"
        return False, f"Azure 语音服务无法连接（{err_msg} · {proxy_desc}）"


def _google_tts_endpoints(cleaned_text: str, voice: str) -> list[tuple[str, dict[str, str], str]]:
    """构造 Google TTS 候选请求端点列表。"""
    query = urllib.parse.quote(cleaned_text)
    speed = "0.24" if voice == "google_ko_slow" else "1"
    params = f"ie=UTF-8&client=tw-ob&tl=ko&ttsspeed={speed}&q={query}"
    return [
        (
            f"https://www.google.com/translate_tts?{params}",
            {"User-Agent": _BASE_USER_AGENT, "Host": "translate.googleapis.com"},
            "www.google.com (GFE)",
        ),
        (
            f"https://translate.googleapis.com/translate_tts?{params}",
            {"User-Agent": _BASE_USER_AGENT},
            "translate.googleapis.com",
        ),
        (
            f"https://translate.google.com/translate_tts?{params}",
            {"User-Agent": _BASE_USER_AGENT},
            "translate.google.com",
        ),
    ]


def probe_google_tts(voice: str = "google_ko", timeout: float = 4.0) -> tuple[bool, str]:
    """实测 Google 韩语发音服务的网络连通性与响应延迟。"""
    proxies = _get_effective_proxies()
    proxy_url = proxies.get("https") or proxies.get("http") or ""
    proxy_desc = f"系统代理 {proxy_url}" if proxy_url else "未检测到系统代理/直连"

    opener = _build_url_opener()
    last_err = "请求超时"
    for url, headers, node_label in _google_tts_endpoints("한국어", voice):
        req = urllib.request.Request(url, headers=headers)
        t0 = time.perf_counter()
        try:
            with opener.open(req, timeout=timeout) as resp:
                data = resp.read()
            elapsed_ms = max(1, round((time.perf_counter() - t0) * 1000))
            if data and len(data) > 256:
                return (
                    True,
                    f"Google 语音服务连接正常（延迟 {elapsed_ms} ms · {node_label} · {proxy_desc}）",
                )
            last_err = "返回音频为空"
        except Exception as exc:
            reason = getattr(exc, "reason", None) or exc
            last_err = str(reason)
            continue

    if "timed out" in last_err.lower() or "10060" in last_err:
        last_err = "握手或连接超时"
    return False, f"Google 语音服务无法连接（{last_err} · {proxy_desc}）"


def probe_tts_network(
    voice: str = DEFAULT_TTS_VOICE,
    azure_key: str = "",
    azure_region: str = "",
    timeout: float = 5.0,
) -> tuple[bool, str]:
    """根据所选语音模式自动探测对应的云端语音网络服务。"""
    if voice.startswith("azure_"):
        return probe_azure_tts(key=azure_key, region=azure_region, voice=voice, timeout=timeout)
    return probe_google_tts(voice=voice, timeout=timeout)


class TTSNetworkProbeWorker(QThread):
    """后台检测 TTS 云端网络状态的线程。"""

    probed = Signal(bool, str)

    def __init__(
        self,
        voice: str = DEFAULT_TTS_VOICE,
        azure_key: str = "",
        azure_region: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.voice = voice or DEFAULT_TTS_VOICE
        self.azure_key = azure_key
        self.azure_region = azure_region

    def run(self):
        ok, detail = probe_tts_network(
            self.voice,
            azure_key=self.azure_key,
            azure_region=self.azure_region,
            timeout=5.0,
        )
        self.probed.emit(ok, detail)


def count_uncached_words(words: list[str], voice: str = DEFAULT_TTS_VOICE) -> int:
    """统计给定韩文单词列表中尚未生成有效本地缓存的词条数量。"""
    uncached = 0
    seen = set()
    for raw in words:
        cleaned = clean_korean_for_tts(raw)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            if not is_cached(cleaned, voice):
                uncached += 1
    return uncached


class BatchTTSPreloadWorker(QThread):
    """后台批量预下载词表发音音频并原子存入缓存。"""

    progress = Signal(int, int, str)  # done_count, total_count, current_word
    finished = Signal(int, int)  # downloaded_count, total_count
    error = Signal(str)

    def __init__(
        self,
        words: list[str],
        voice: str = DEFAULT_TTS_VOICE,
        azure_key: str = "",
        azure_region: str = "",
        delay_sec: float = 0.08,
        parent=None,
    ):
        super().__init__(parent)
        self.words = list(words)
        self.voice = voice or DEFAULT_TTS_VOICE
        self.azure_key = azure_key
        self.azure_region = azure_region
        self.delay_sec = max(0.02, delay_sec)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            TTS_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.error.emit(str(exc))
            return

        pending = []
        seen = set()
        for raw in self.words:
            cleaned = clean_korean_for_tts(raw)
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                if not is_cached(cleaned, self.voice):
                    pending.append((raw, cleaned, cache_path_for(cleaned, self.voice)))

        total = len(pending)
        if total == 0:
            self.finished.emit(0, 0)
            return

        downloaded = 0
        for idx, (raw, cleaned, target_path) in enumerate(pending, 1):
            if self._cancelled:
                return

            self.progress.emit(idx - 1, total, raw)

            if is_cached(cleaned, self.voice):
                downloaded += 1
                continue

            tmp_path = target_path.with_suffix(f".batch_{idx}.tmp")
            try:
                if self.voice.startswith("azure_"):
                    audio = synthesize_azure(
                        cleaned,
                        voice=self.voice,
                        key=self.azure_key,
                        region=self.azure_region,
                        timeout=7.0,
                    )
                    if audio and len(audio) > 256:
                        tmp_path.write_bytes(audio)
                        os.replace(tmp_path, target_path)
                        downloaded += 1
                else:
                    opener = _build_url_opener()
                    for url, headers, _ in _google_tts_endpoints(cleaned, self.voice):
                        if self._cancelled:
                            break
                        req = urllib.request.Request(url, headers=headers)
                        try:
                            with opener.open(req, timeout=5.0) as resp:
                                data = resp.read()
                            if data and len(data) > 256:
                                tmp_path.write_bytes(data)
                                os.replace(tmp_path, target_path)
                                downloaded += 1
                                break
                        except Exception:
                            continue
            except Exception:
                pass
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass

            if self._cancelled:
                return

            self.progress.emit(idx, total, raw)
            if self.delay_sec > 0:
                time.sleep(self.delay_sec)

        if not self._cancelled:
            self.finished.emit(downloaded, total)


class _TTSFetchWorker(QThread):
    """后台拉取云端 TTS 韩语音频并原子写入本地缓存目录。"""

    completed = Signal(int, str, str, str, bool)  # request_id, cleaned_text, file_path, error, manual

    def __init__(
        self,
        request_id: int,
        cleaned_text: str,
        voice: str,
        target_path: Path,
        azure_key: str = "",
        azure_region: str = "",
        manual: bool = False,
    ):
        super().__init__()
        self.request_id = request_id
        self.cleaned_text = cleaned_text
        self.voice = voice or DEFAULT_TTS_VOICE
        self.target_path = Path(target_path)
        self.azure_key = azure_key
        self.azure_region = azure_region
        self.manual = bool(manual)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            return
        try:
            TTS_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.completed.emit(
                self.request_id, self.cleaned_text, "", str(exc), self.manual
            )
            return

        tmp_path = self.target_path.with_suffix(f".{self.request_id}.tmp")
        try:
            if self.voice.startswith("azure_"):
                ok = self._fetch_azure_tts(tmp_path)
                service_name = "Azure"
            else:
                ok = self._fetch_google_tts(tmp_path)
                service_name = "Google"

            if self._cancelled:
                self._cleanup_tmp(tmp_path)
                return

            if ok and tmp_path.is_file() and tmp_path.stat().st_size > 256:
                os.replace(tmp_path, self.target_path)
                self.completed.emit(
                    self.request_id,
                    self.cleaned_text,
                    str(self.target_path),
                    "",
                    self.manual,
                )
                return

            self._cleanup_tmp(tmp_path)
            self.completed.emit(
                self.request_id,
                self.cleaned_text,
                "",
                f"{service_name} 语音服务连接超时或未返回有效音频",
                self.manual,
            )
        except Exception as exc:
            self._cleanup_tmp(tmp_path)
            if not self._cancelled:
                self.completed.emit(
                    self.request_id, self.cleaned_text, "", str(exc), self.manual
                )

    def _fetch_azure_tts(self, tmp_path: Path) -> bool:
        if self._cancelled:
            return False
        try:
            audio = synthesize_azure(
                self.cleaned_text,
                voice=self.voice,
                key=self.azure_key,
                region=self.azure_region,
                timeout=6.0,
            )
            if audio and len(audio) > 256:
                tmp_path.write_bytes(audio)
                return True
        except Exception:
            self._cleanup_tmp(tmp_path)
        return False

    def _fetch_google_tts(self, tmp_path: Path) -> bool:
        """从 Google Translate TTS 拉取韩语 MP3（支持标准语速与慢速跟读）。"""
        opener = _build_url_opener()
        for url, headers, _node in _google_tts_endpoints(self.cleaned_text, self.voice):
            if self._cancelled:
                return False
            req = urllib.request.Request(url, headers=headers)
            try:
                with opener.open(req, timeout=4.5) as resp:
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
    """单词发音控制器：统一调度本地缓存播放、后台云端 TTS 拉取与系统离线语音兜底。"""

    # level ("info" | "warning" | "danger"), message
    notice = Signal(str, str)

    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.database = database
        self._request_seq = 0
        self._workers = set()
        self._offline_warned = False
        self._last_played_path: Path | None = None

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(1.0)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.errorOccurred.connect(self._on_player_error)
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

    def speak(self, raw_korean: str, manual: bool = False) -> bool:
        """朗读给定韩语词条。`manual=True` 表示用户主动点击发音/试听按钮。"""
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
        self._start_fetch(req_id, cleaned, voice, cached_file, manual=manual)
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
        self._last_played_path = path
        self.player.stop()
        # 先清空 source 再重设，保证同一缓存文件连续点击时始终从头播放
        self.player.setSource(QUrl())
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def _on_player_error(self, _error, error_string: str):
        # 若缓存文件损坏导致解码失败，自动移除损坏文件以便下次重新拉取
        bad_path = self._last_played_path
        self._last_played_path = None
        if bad_path is not None:
            try:
                if bad_path.is_file():
                    bad_path.unlink()
            except OSError:
                pass
        if error_string:
            self.notice.emit("warning", f"音频播放失败：{error_string}")

    def _start_fetch(
        self, req_id: int, cleaned: str, voice: str, target_path: Path, manual: bool = False
    ):
        for w in list(self._workers):
            if w.isRunning():
                w.cancel()

        azure_key = ""
        azure_region = ""
        if voice.startswith("azure_"):
            azure_key = self.database.get_azure_speech_key()
            azure_region = self.database.get_azure_speech_region()

        worker = _TTSFetchWorker(
            req_id,
            cleaned,
            voice,
            target_path,
            azure_key=azure_key,
            azure_region=azure_region,
            manual=manual,
        )
        self._workers.add(worker)
        worker.completed.connect(self._on_fetch_completed)
        worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
        worker.start()

    def _cleanup_worker(self, worker: _TTSFetchWorker):
        self._workers.discard(worker)
        worker.deleteLater()

    def _on_fetch_completed(
        self, req_id: int, cleaned: str, file_path: str, error: str, manual: bool = False
    ):
        # 如果用户已经切到了下一个词，只静默保留缓存文件，不再插播旧词
        if req_id != self._request_seq:
            return

        if file_path and os.path.isfile(file_path):
            self._offline_warned = False
            self._play_file(Path(file_path))
            return

        # 联网合成失败：自动退回系统本地韩语语音（PRODUCT_SPEC 8.6 / 12.2 F3）
        service_label = "Azure" if self.database.get_tts_voice().startswith("azure_") else "Google"
        ok_local = self._speak_local(cleaned, explicit_local=False)
        if manual or not self._offline_warned:
            self._offline_warned = True
            if ok_local:
                self.notice.emit(
                    "warning",
                    f"当前无法连接 {service_label} 语音服务，已切换为系统离线韩语发音",
                )
            else:
                self.notice.emit(
                    "warning",
                    f"无法连接 {service_label} 语音服务（可在设置页排查），且系统未安装离线韩语语音包",
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

