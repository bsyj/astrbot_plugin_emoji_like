import asyncio
import random

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.components import Face, Image, Reply
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .core.config import PluginConfig
from .core.emotion import EmotionJudger


class EmojiLikePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = PluginConfig(config, context)
        self.judger = EmotionJudger(self.cfg)

    async def _emoji_like(
        self,
        event: AiocqhttpMessageEvent,
        emoji_ids: list[int],
        message_id: int | str | None = None,
    ):
        logger.info(f"贴表情: {emoji_ids}")
        message_id = message_id or event.message_obj.message_id
        emoji_ids = emoji_ids[: self.cfg.max_emoji_count]
        for emoji_id in set(emoji_ids):
            try:
                await event.bot.set_msg_emoji_like(
                    message_id=message_id,
                    emoji_id=emoji_id,
                    set=True,
                )
            except Exception as e:
                logger.warning(f"贴表情失败: {e}")

            await asyncio.sleep(self.cfg.emoji_interval)

    async def _follow_reaction_notice(self, event: AiocqhttpMessageEvent) -> bool:
        raw = getattr(event.message_obj, "raw_message", None)
        if not raw or raw.get("post_type") != "notice":
            return False
        if raw.get("notice_type") not in ("group_msg_emoji_like", "reaction"):
            return False
        if raw.get("is_add") is False:
            return False
        if str(raw.get("user_id")) == str(event.get_self_id()):
            return False
        if random.random() >= self.cfg.emoji_reaction_follow_prob:
            return False

        message_id = raw.get("message_id")
        if not message_id:
            return False

        for item in raw.get("likes") or []:
            emoji_id = item.get("emoji_id") if isinstance(item, dict) else None
            if str(emoji_id).isdigit():
                await self._emoji_like(event, [int(emoji_id)], message_id=message_id)
                return True
        return False

    @filter.command("贴表情")
    async def on_command(self, event: AiocqhttpMessageEvent, emojiNum: int = 5):
        """贴表情 <数量>"""
        chain = event.get_messages()
        if not chain:
            return
        reply = chain[0] if isinstance(chain[0], Reply) else None
        if not reply or not reply.chain or not reply.text or not reply.id:
            return

        images = [seg.url for seg in reply.chain if isinstance(seg, Image) and seg.url]

        emotion = await self.judger.judge_emotion(
            event,
            text=reply.text,
            image_urls=images,
            labels=self.cfg.emotion_labels,
        )
        emoji_ids = self.cfg.get_emoji_ids(emotion, need_count=int(emojiNum))
        await self._emoji_like(event, emoji_ids, message_id=reply.id)
        event.stop_event()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_message(self, event: AiocqhttpMessageEvent):
        """群消息监听"""
        if await self._follow_reaction_notice(event):
            event.stop_event()
            return

        if event.is_at_or_wake_command:
            return

        # 跟随已有表情
        chain = event.get_messages()
        emoji_ids = [seg.id for seg in chain if isinstance(seg, Face)]
        if emoji_ids and random.random() < self.cfg.emoji_follow_prob:
            await self._emoji_like(event, emoji_ids)

        # 主动表情
        msg = event.message_str
        if msg and random.random() < self.cfg.emoji_like_prob:
            asyncio.create_task(self.async_emoji_like_by_emotion(event, msg))

    async def async_emoji_like_by_emotion(
        self,
        event: AiocqhttpMessageEvent,
        text: str,
        image_urls: list[str] | None = None,
        message_id: int | str | None = None,
    ):
        emotion = await self.judger.judge_emotion(
            event,
            text=text,
            image_urls=image_urls,
            labels=self.cfg.emotion_labels,
        )
        emoji_ids = self.cfg.get_emoji_ids(emotion, need_count=1)
        await self._emoji_like(event, emoji_ids, message_id=message_id)
