from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import random
import re
import shutil
import subprocess
from pathlib import Path
from typing import AsyncGenerator

import httpx

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain, Record
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.config.astrbot_config import AstrBotConfig

TTS_ENDPOINT = "https://v1.wusound.cn/api/tts/simple-generate"
VOICE_ENDPOINT = "https://v1.wusound.cn/api/voice"

# 悟声 simple-generate 接口官方 preset 可选值
PRESET_CHOICES = (
    "fast_standard",
    "fast_premium",
    "fast_ultra",
    "balance",
    "stable",
    "expressive",
)

CMD_PATTERN = re.compile(r"^[/!！]?\s*(语音|tts|念|说话)\s*", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://\S+")
NOISE_PATTERN = re.compile(r"[*#`_>|~]")


def hit_probability(probability: int) -> bool:
    """按百分比概率掷一次骰子：100 必中，0 必不中。"""
    if probability >= 100:
        return True
    if probability <= 0:
        return False
    return random.random() * 100 < probability


class WusoundError(Exception):
    """悟声接口返回的业务错误。"""


class WusoundClient:
    """悟声开放接口的最小封装。

    覆盖 simple-generate 的全部官方参数：
    voiceId / text / preset / language / speechRate / model / srt / stream
    """

    def __init__(self, api_key: str, timeout: float = 30.0) -> None:
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self._http: httpx.AsyncClient | None = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        return self._http

    async def close(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    def _auth_headers(self, *, json_body: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _unwrap(resp: httpx.Response) -> dict:
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise WusoundError(f"HTTP {resp.status_code}：{resp.text[:200]}") from exc
        status = data.get("status")
        if resp.status_code >= 400 or (status is not None and status != 200):
            code = data.get("code") or status
            message = data.get("message") or resp.text[:200]
            raise WusoundError(f"{code}：{message}")
        return data

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        *,
        preset: str = "balance",
        language: str = "auto",
        speech_rate: float = 1.0,
        model: str = "",
        srt: bool = False,
        stream: bool = False,
    ) -> bytes:
        """合成一段语音，返回音频（mp3）字节。

        覆盖悟声 simple-generate 的全部官方参数。
        """
        payload: dict = {
            "voiceId": voice_id,
            "text": text,
            "preset": preset,
            "language": language,
            "speechRate": speech_rate,
            "srt": srt,
            "stream": stream,
        }
        if model:
            payload["model"] = model

        resp = await self.http.post(
            TTS_ENDPOINT, headers=self._auth_headers(json_body=True), json=payload
        )
        data = self._unwrap(resp)
        detail = data.get("data") or {}
        audio_url = detail.get("audio") or detail.get("streamUrl")
        if not audio_url:
            raise WusoundError("接口没有返回音频地址")
        audio_resp = await self.http.get(audio_url)
        audio_resp.raise_for_status()
        return audio_resp.content

    async def list_voices(self) -> list[dict]:
        """拉取当前账号下的语音角色列表。"""
        resp = await self.http.get(
            VOICE_ENDPOINT,
            headers=self._auth_headers(),
            params={"from": "undefined", "showMarket": "undefined"},
        )
        data = self._unwrap(resp)
        voices = data.get("data") or []
        return voices if isinstance(voices, list) else []

    async def guess_voice_id(self, voice_id: str) -> tuple[str, str] | None:
        """用填错的 ID / 角色名反查，返回 (可用于生成的 ID, 角色名)。"""
        try:
            voices = await self.list_voices()
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[wusound] 拉取角色列表失败，跳过 ID 纠正: {exc!r}")
            return None

        for item in voices:
            if voice_id in (item.get("id"), item.get("idForGenerate")):
                alt = (
                    item.get("idForGenerate")
                    if voice_id == item.get("id")
                    else item.get("id")
                )
                if alt:
                    return str(alt), str(item.get("name") or "")
        for item in voices:
            if item.get("name") == voice_id:
                alt = item.get("idForGenerate") or item.get("id")
                if alt:
                    return str(alt), str(item.get("name") or "")
        return None


@register(
    "astrbot_plugin_wusound_tts",
    "unmia1",
    "调用悟声语音合成，把文字变成 QQ 语音",
    "1.2.0",
)
class WusoundTTSPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        # config 允许为 None：当插件目录缺失 _conf_schema.json（例如从 GitHub
        # 导入时漏传了该文件）时，AstrBot 会以单参数实例化，这里用空配置兜底，
        # 保证插件仍可加载，只是所有配置回落到内置默认值。
        if config is not None:
            self.cfg = config
        else:
            try:
                data_dir = Path(StarTools.get_data_dir())
            except Exception:  # noqa: BLE001
                data_dir = Path("data") / "astrbot_plugin_wusound_tts"
            fallback_path = data_dir / "astrbot_plugin_wusound_tts_fallback.json"
            self.cfg = AstrBotConfig(
                config_path=str(fallback_path),
                schema={},
            )

        try:
            self.data_dir = Path(StarTools.get_data_dir())
        except Exception:  # noqa: BLE001
            self.data_dir = Path("data") / "astrbot_plugin_wusound_tts"
        self.cache_dir = self.data_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.client = WusoundClient(
            str(self.cfg.get("api_key", "") or ""),
            timeout=float(self.cfg.get("timeout", 30) or 30),
        )

    async def terminate(self) -> None:
        await self.client.close()

    # ---------------------------------------------------------------- 属性

    @property
    def voice_id(self) -> str:
        return str(self.cfg.get("voice_id", "") or "").strip()

    @property
    def max_chars(self) -> int:
        try:
            return max(20, int(self.cfg.get("max_chars", 200) or 200))
        except (TypeError, ValueError):
            return 200

    @property
    def auto_probability(self) -> int:
        """自动语音的触发概率，0~100。"""
        try:
            value = int(float(self.cfg.get("auto_tts_probability", 100)))
        except (TypeError, ValueError):
            return 100
        return max(0, min(100, value))

    @property
    def speech_rate(self) -> float:
        try:
            return max(0.1, min(5.0, float(self.cfg.get("speech_rate", 1.0) or 1.0)))
        except (TypeError, ValueError):
            return 1.0

    @property
    def preset(self) -> str:
        return str(self.cfg.get("preset", "balance") or "balance")

    @property
    def language(self) -> str:
        return str(self.cfg.get("language", "auto") or "auto")

    @property
    def model(self) -> str:
        return str(self.cfg.get("model", "") or "").strip()

    @property
    def srt_enabled(self) -> bool:
        return bool(self.cfg.get("srt", False))

    @property
    def stream_enabled(self) -> bool:
        return bool(self.cfg.get("stream", False))

    @property
    def voice_padding(self) -> float:
        """语音末尾追加的静音时长（秒），用于延长语音长度。0 表示不追加。"""
        try:
            value = float(self.cfg.get("voice_padding", 0) or 0)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(10.0, value))

    # ---------------------------------------------------------------- 命令

    @filter.command("语音", alias={"tts", "念", "说话"})
    async def cmd_speech(self, event: AstrMessageEvent):
        """把一段文字合成语音发出来，用法：/语音 你好啊"""
        if not self.client.api_key:
            yield event.plain_result(
                "还没配置悟声 API Key。\n"
                "去 https://www.wusound.cn/apiKey 创建一个，填进插件配置的 api_key。"
            )
            return
        if not self.voice_id:
            yield event.plain_result(
                "还没设置默认声音。先用 /语音角色 看可用的声音 ID，填进 voice_id。"
            )
            return

        text = self._extract_text(event)
        if not text:
            yield event.plain_result(
                "用法：/语音 要念的文字\n"
                "例：/语音 今天天气不错，出来晒晒太阳"
            )
            return

        truncated = len(text) > self.max_chars
        if truncated:
            text = text[: self.max_chars]
            yield event.plain_result(f"文字有点长，只念前 {self.max_chars} 个字。")

        try:
            audio_path, cached = await self._synthesize(text)
        except WusoundError as exc:
            logger.warning(f"[wusound] 合成失败: {exc}")
            yield event.plain_result(f"语音合成失败：{exc}")
            return
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[wusound] 合成异常: {exc!r}")
            yield event.plain_result("语音合成出错了，稍后再试试。")
            return

        if cached:
            logger.debug("[wusound] 命中本地缓存")

        async for item in self._deliver(event, audio_path):
            yield item

    @filter.command("语音角色", alias={"声音列表"})
    async def cmd_voices(self, event: AstrMessageEvent):
        """列出账号下可用的声音及其 ID、描述、模型版本、风格 prompts"""
        if not self.client.api_key:
            yield event.plain_result("还没配置悟声 API Key。")
            return
        try:
            voices = await self.client.list_voices()
        except Exception as exc:  # noqa: BLE001
            yield event.plain_result(f"拉取声音列表失败：{exc}")
            return

        if not voices:
            yield event.plain_result(
                "账号下还没有声音角色。去悟声控制台创建一个，或者从声音市场添加一个。"
            )
            return

        lines = [f"可用声音共 {len(voices)} 个，把 ID 填进插件配置的 voice_id："]
        for idx, item in enumerate(voices[:20], start=1):
            name = item.get("name") or "未命名"
            vid = item.get("idForGenerate") or item.get("id") or "-"
            desc = (item.get("description") or "").strip()
            version = (item.get("version") or item.get("metadata", {}).get("version") or "").strip()
            prompts = self._extract_prompts(item)
            line = f"{idx}. {name}  {vid}"
            if version:
                line += f"  [{version}]"
            if prompts:
                line += f"  风格:{','.join(prompts[:5])}"
            if desc:
                line += f"  {desc[:30]}"
            lines.append(line)
        if len(voices) > 20:
            lines.append(f"……另有 {len(voices) - 20} 个未显示")
        yield event.plain_result("\n".join(lines))

    @filter.command("语音清理")
    async def cmd_clear_cache(self, event: AstrMessageEvent):
        """清空本地语音缓存"""
        removed = 0
        for file in self.cache_dir.glob("*.mp3"):
            try:
                file.unlink()
                removed += 1
            except OSError:
                continue
        yield event.plain_result(f"已清理 {removed} 个缓存的语音文件。")

    @filter.command("语音概率", alias={"自动语音概率"})
    async def cmd_probability(self, event: AstrMessageEvent):
        """查看或设置自动语音的触发概率，用法：/语音概率 30"""
        raw = event.message_str or ""
        match = re.search(r"(\d{1,3})", raw)
        if not match:
            enabled = "已开启" if bool(self.cfg.get("auto_tts", False)) else "未开启"
            yield event.plain_result(
                f"自动语音：{enabled}\n"
                f"当前概率：{self.auto_probability}%\n"
                "用法：/语音概率 30（0~100，100 每条回复都发语音，0 相当于关闭）"
            )
            return

        value = max(0, min(100, int(match.group(1))))
        self.cfg["auto_tts_probability"] = value
        saved = "已写入配置文件"
        try:
            await self.cfg.save_config_async()
        except Exception as exc:  # noqa: BLE001
            saved = f"但保存失败（{exc!r}），重启后可能丢失"
        tip = (
            ""
            if bool(self.cfg.get("auto_tts", False))
            else "\n注意：自动语音总开关（auto_tts）还是关的，要在 WebUI 插件配置里打开才会生效。"
        )
        yield event.plain_result(f"自动语音概率已设为 {value}%，{saved}。{tip}")

    @filter.command("语音时长", alias={"语音延长", "语音静音"})
    async def cmd_padding(self, event: AstrMessageEvent):
        """查看或设置语音末尾追加的静音时长，用法：/语音时长 1"""
        raw = event.message_str or ""
        match = re.search(r"(\d+(?:\.\d+)?)", raw)
        if not match:
            yield event.plain_result(
                f"当前语音末尾追加静音：{self.voice_padding:g} 秒\n"
                "用法：/语音时长 1（0~10 秒，0 表示不追加，支持小数）\n"
                "例如一段 3 秒的语音，追加 1 秒静音后发送出来就是 4 秒。"
            )
            return

        value = max(0.0, min(10.0, float(match.group(1))))
        self.cfg["voice_padding"] = value
        saved = "已写入配置文件"
        try:
            await self.cfg.save_config_async()
        except Exception as exc:  # noqa: BLE001
            saved = f"但保存失败（{exc!r}），重启后可能丢失"
        yield event.plain_result(
            f"语音末尾静音已设为 {value:g} 秒，{saved}。\n"
            "之后合成的语音都会在末尾追加这段静音，让语音变长。"
        )

    # ------------------------------------------------------- 自动语音钩子

    @filter.on_decorating_result()
    async def auto_speech(self, event: AstrMessageEvent):
        """把 AI 的回复自动转成语音（需在配置里开启 auto_tts）。"""
        if not bool(self.cfg.get("auto_tts", False)):
            return
        if not self.client.api_key or not self.voice_id:
            return

        result = event.get_result()
        if not result or not result.chain:
            return
        try:
            if not result.is_llm_result():
                return
        except Exception:  # noqa: BLE001
            return
        if any(isinstance(comp, Record) for comp in result.chain):
            return

        text = "".join(
            comp.text
            for comp in result.chain
            if isinstance(comp, Plain) and getattr(comp, "text", None)
        ).strip()
        if not text:
            return
        if len(text) > self.max_chars:
            logger.debug(f"[wusound] 回复过长（{len(text)} 字），跳过自动语音")
            return

        whitelist = {
            item.strip()
            for item in str(self.cfg.get("auto_tts_groups", "") or "")
            .replace("，", ",")
            .split(",")
            if item.strip()
        }
        if whitelist:
            group_id = str(event.get_group_id() or "")
            if not group_id or group_id not in whitelist:
                return

        probability = self.auto_probability
        if not hit_probability(probability):
            logger.debug(f"[wusound] 本次未命中自动语音概率（{probability}%），保持文字")
            return

        try:
            audio_path, _ = await self._synthesize(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[wusound] 自动语音跳过: {exc!r}")
            return

        audio_path = await self._apply_padding(audio_path)
        record = Record.fromFileSystem(str(audio_path))
        if bool(self.cfg.get("auto_tts_keep_text", True)):
            result.chain = [*result.chain, record]
        else:
            result.chain = [record]

    # ---------------------------------------------------------------- 内部

    def _extract_text(self, event: AstrMessageEvent) -> str:
        raw = (event.message_str or "").strip()
        return CMD_PATTERN.sub("", raw, count=1).strip()

    @staticmethod
    def _clean_text(text: str) -> str:
        text = URL_PATTERN.sub("", text)
        text = NOISE_PATTERN.sub("", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _extract_prompts(item: dict) -> list[str]:
        """从声音对象里提取可用的风格 prompts。"""
        prompts = item.get("prompts") or []
        names: list[str] = []
        for p in prompts:
            if isinstance(p, dict):
                name = p.get("name") or p.get("id") or ""
                if name and name not in names:
                    names.append(str(name))
        return names

    async def _synthesize(self, text: str) -> tuple[Path, bool]:
        """合成并落盘，返回 (音频路径, 是否命中缓存)。"""
        text = self._clean_text(text)
        if not text:
            raise WusoundError("没有可念的文字")

        voice_id = self.voice_id
        params = {
            "voice": voice_id,
            "text": text,
            "rate": self.speech_rate,
            "preset": self.preset,
            "lang": self.language,
            "model": self.model,
            "srt": self.srt_enabled,
            "stream": self.stream_enabled,
        }

        signature = json.dumps(params, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()
        target = self.cache_dir / f"{digest}.mp3"

        if (
            bool(self.cfg.get("cache_enabled", True))
            and target.exists()
            and target.stat().st_size > 0
        ):
            return target, True

        try:
            audio = await self.client.synthesize(
                text,
                voice_id,
                preset=self.preset,
                language=self.language,
                speech_rate=self.speech_rate,
                model=self.model,
                srt=self.srt_enabled,
                stream=self.stream_enabled,
            )
        except WusoundError:
            fixed = await self.client.guess_voice_id(voice_id)
            if not fixed:
                raise
            logger.info(f"[wusound] 声音 ID 自动纠正为 {fixed[0]}（{fixed[1]}）")
            audio = await self.client.synthesize(
                text,
                fixed[0],
                preset=self.preset,
                language=self.language,
                speech_rate=self.speech_rate,
                model=self.model,
                srt=self.srt_enabled,
                stream=self.stream_enabled,
            )

        if not audio:
            raise WusoundError("返回的音频是空的")
        target.write_bytes(audio)
        return target, False

    async def _apply_padding(self, audio_path: Path) -> Path:
        """在语音末尾追加静音，返回处理后的文件路径。

        用 ffmpeg 的 apad 滤镜追加指定时长的静音（若 voice_padding <= 0 或
        找不到 ffmpeg，则原样返回）。处理后的文件落在缓存目录，带 padding 标记，
        便于复用；无 ffmpeg 时静默降级为原音频，不影响正常发送。
        """
        padding = self.voice_padding
        if padding <= 0:
            return audio_path

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("[wusound] 未找到 ffmpeg，跳过语音延长")
            return audio_path

        stem = audio_path.stem
        padded = audio_path.with_name(f"{stem}_pad{padding:g}.mp3")
        if padded.exists() and padded.stat().st_size > 0:
            return padded

        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg,
                "-y",
                "-i",
                str(audio_path),
                "-af",
                f"apad=pad_dur={padding:g}",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "9",
                str(padded),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if proc.returncode == 0 and padded.exists() and padded.stat().st_size > 0:
                return padded
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[wusound] 语音延长失败，改用原音频: {exc!r}")
        return audio_path

    async def _deliver(
        self, event: AstrMessageEvent, audio_path: Path
    ) -> AsyncGenerator:
        """按配置的方式把语音发出去。"""
        audio_path = await self._apply_padding(audio_path)
        mode = str(self.cfg.get("send_mode", "component") or "component").lower()
        if mode == "raw":
            try:
                await self._send_raw(event, audio_path)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[wusound] 原生发送失败，改用消息组件: {exc!r}")
        elif shutil.which("ffmpeg") is None:
            logger.warning("[wusound] 系统里找不到 ffmpeg，改用原生 base64 发送 mp3")
            try:
                await self._send_raw(event, audio_path)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[wusound] 原生发送失败，改用消息组件: {exc!r}")
        yield event.chain_result([Record.fromFileSystem(str(audio_path))])

    @staticmethod
    async def _send_raw(event: AstrMessageEvent, audio_path: Path) -> None:
        encoded = base64.b64encode(audio_path.read_bytes()).decode()
        segment = [{"type": "record", "data": {"file": f"base64://{encoded}"}}]
        group_id = event.get_group_id()
        if group_id:
            await event.bot.call_action(
                "send_group_msg", group_id=int(group_id), message=segment
            )
        else:
            await event.bot.call_action(
                "send_private_msg",
                user_id=int(event.get_sender_id()),
                message=segment,
            )
