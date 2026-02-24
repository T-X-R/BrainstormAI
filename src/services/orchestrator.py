"""Session runtime orchestrator with always-on parallel agent workers."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

from src.config.settings import get_settings
from src.domain.schemas import AgentAction, AgentDecision, AgentInfo
from src.services.agent_decision import decide_agent_action
from src.services.agent_reply import generate_agent_reply


@dataclass
class ChatMessage:
    id: str
    author_type: str
    author_id: str | None
    author_name: str
    content: str
    target_message_id: str | None = None
    target_author_name: str | None = None


@dataclass
class AgentState:
    info: AgentInfo
    last_spoke_at: float = 0.0
    last_seen_message_version: int = 0


@dataclass
class SessionOrchestrator:
    session_id: str
    topic: str
    agents: list[AgentState] = field(default_factory=list)
    messages: list[ChatMessage] = field(default_factory=list)
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    _agent_workers: list[asyncio.Task] = field(default_factory=list)
    _decision_tasks: set[asyncio.Task] = field(default_factory=set)
    _generation_tasks: set[asyncio.Task] = field(default_factory=set)
    _generation_output_started: dict[asyncio.Task, bool] = field(default_factory=dict)
    _monitor_task: asyncio.Task | None = None
    _message_condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    _message_version: int = 0
    _runtime_started: bool = False
    _ended: bool = False
    _end_reason: str | None = None
    _pending_decisions: int = 0
    _started_at: float = 0.0
    _last_activity_at: float = 0.0
    _last_global_speak_at: float = 0.0
    _total_ai_messages: int = 0
    _recent_key_points: deque[str] = field(default_factory=lambda: deque(maxlen=8))
    _decision_semaphore: asyncio.Semaphore | None = None
    _generation_paused: bool = False
    _paused_since: float | None = None

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        if self.on_event:
            await self.on_event({"type": event_type, "data": data})

    def add_agent(self, info: AgentInfo) -> None:
        self.agents.append(AgentState(info=info))

    def add_message(self, msg: ChatMessage) -> None:
        self.messages.append(msg)
        self._message_version += 1

    async def start_runtime(self) -> None:
        if self._runtime_started or self._ended:
            return
        self._runtime_started = True
        now = time.time()
        self._started_at = now
        self._last_activity_at = now
        self._decision_semaphore = asyncio.Semaphore(2)

        for agent_state in self.agents:
            worker = asyncio.create_task(self._agent_worker(agent_state))
            self._agent_workers.append(worker)
        self._monitor_task = asyncio.create_task(self._monitor_lifecycle())

        await self.emit(
            "runtime_started",
            {
                "session_id": self.session_id,
                "agent_count": len(self.agents),
            },
        )

    async def stop(self, force: bool = False) -> None:
        """Stop current generation tasks; force=True cancels streaming too."""
        if force:
            # Pause follow-up generations until next user message arrives.
            self._generation_paused = True
            if self._paused_since is None:
                self._paused_since = time.time()
        for task in list(self._decision_tasks):
            if not task.done():
                task.cancel()
        for task in list(self._generation_tasks):
            if not task.done():
                if force or not self._generation_output_started.get(task, False):
                    task.cancel()
        await asyncio.sleep(0)

    async def handle_new_message(self, msg: ChatMessage) -> None:
        await self.start_runtime()
        if msg.author_type == "user":
            # A new user turn resumes AI generation.
            self._generation_paused = False
            self._paused_since = None
            await self.stop(force=False)
        self.add_message(msg)
        self._last_activity_at = time.time()
        await self._notify_new_message()

    def _get_recent_messages(self, limit: int = 20) -> list[ChatMessage]:
        return self.messages[-limit:] if len(self.messages) > limit else self.messages

    async def shutdown(self, reason: str = "session_ended", emit_event: bool = False) -> None:
        if self._ended:
            return
        self._ended = True
        self._end_reason = reason

        await self.stop(force=True)
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        current = asyncio.current_task()
        for worker in self._agent_workers:
            if worker is not current and not worker.done():
                worker.cancel()

        for worker in self._agent_workers:
            if worker is current:
                continue
            with contextlib.suppress(asyncio.CancelledError):
                await worker

        if self._monitor_task and self._monitor_task is not current:
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task

        if emit_event:
            await self.emit(
                "session_ended",
                {
                    "session_id": self.session_id,
                    "reason": reason,
                },
            )

    async def _notify_new_message(self) -> None:
        async with self._message_condition:
            self._message_condition.notify_all()

    async def _agent_worker(self, agent_state: AgentState) -> None:
        while not self._ended:
            has_new = await self._wait_for_new_message(agent_state)
            if self._ended:
                return
            if not has_new:
                continue
            if self._generation_paused:
                # While paused, workers should not start any new decision calls.
                continue

            settings = get_settings()
            cfg = settings.session
            now = time.time()
            cooldown_active = (now - agent_state.last_spoke_at) < cfg.agent_cooldown_seconds
            decision_task: asyncio.Task | None = None

            try:
                self._pending_decisions += 1
                logger.info("Agent {} is deciding", agent_state.info.nickname)
                recent_messages = self._get_recent_messages(limit=50)
                recent_for_prompt = [
                    {
                        "id": m.id,
                        "author_name": m.author_name,
                        "content": (m.content or ""),
                        "target_author_name": m.target_author_name,
                    }
                    for m in recent_messages
                ]
                last_speaker = recent_messages[-1].author_name if recent_messages else None
                decision_task = asyncio.create_task(
                    decide_agent_action(
                        agent_info=agent_state.info,
                        topic=self.topic,
                        recent_messages=recent_for_prompt,
                        last_speaker_name=last_speaker,
                        cooldown_active=cooldown_active,
                        decision_semaphore=self._decision_semaphore,
                    )
                )
                self._decision_tasks.add(decision_task)
                decision = await decision_task
                logger.info("Agent {} decision: {}", agent_state.info.nickname, decision)
            except asyncio.CancelledError:
                logger.info("Decision cancelled for worker {}", agent_state.info.nickname)
                continue
            except Exception as exc:
                logger.error("Agent {} decision failed: {}", agent_state.info.nickname, exc)
                decision = AgentDecision(action=AgentAction.SILENT)
            finally:
                if decision_task is not None:
                    self._decision_tasks.discard(decision_task)
                self._pending_decisions = max(0, self._pending_decisions - 1)
            await self.emit(
                "status",
                {
                    "agent_id": agent_state.info.id,
                    "nickname": agent_state.info.nickname,
                    "action": decision.action.value,
                    "reason": decision.reason,
                    "target": decision.target_author_name,
                    "confidence": decision.confidence,
                },
            )

            if decision.action == AgentAction.SILENT:
                continue
            if self._generation_paused:
                continue
            if not self._approve_speech(agent_state, decision):
                continue

            generation_task = asyncio.create_task(self._generate_reply(agent_state, decision))
            self._generation_tasks.add(generation_task)
            self._generation_output_started[generation_task] = False
            try:
                await generation_task
            except asyncio.CancelledError:
                logger.info("Generation cancelled for worker {}", agent_state.info.nickname)
            finally:
                self._generation_tasks.discard(generation_task)
                self._generation_output_started.pop(generation_task, None)

    async def _wait_for_new_message(self, agent_state: AgentState) -> bool:
        observed = agent_state.last_seen_message_version
        try:
            async with self._message_condition:
                await asyncio.wait_for(
                    self._message_condition.wait_for(
                        lambda: self._ended or self._message_version > observed
                    ),
                    timeout=1.0,
                )
        except TimeoutError:
            return False

        if self._ended:
            return False
        agent_state.last_seen_message_version = self._message_version
        return True

    def _approve_speech(self, agent_state: AgentState, decision: AgentDecision) -> bool:
        settings = get_settings()
        cfg = settings.session
        now = time.time()

        if (now - agent_state.last_spoke_at) < cfg.agent_cooldown_seconds:
            return False
        if cfg.global_speak_interval_seconds > 0 and (now - self._last_global_speak_at) < cfg.global_speak_interval_seconds:
            return False
        if cfg.max_total_ai_messages > 0 and self._total_ai_messages >= cfg.max_total_ai_messages:
            return False

        key_points = (decision.key_points or "").strip().lower()
        if key_points and key_points in self._recent_key_points:
            return False

        self._last_global_speak_at = now
        if key_points:
            self._recent_key_points.append(key_points)
        return True

    async def _monitor_lifecycle(self) -> None:
        settings = get_settings()
        cfg = settings.session
        while not self._ended:
            await asyncio.sleep(1.0)
            now = time.time()
            if cfg.max_total_ai_messages > 0 and self._total_ai_messages >= cfg.max_total_ai_messages:
                await self.shutdown(reason="max_total_ai_messages", emit_event=True)
                return
            if self._generation_paused:
                if cfg.pause_timeout_seconds > 0 and self._paused_since is not None:
                    paused_for = now - self._paused_since
                    if paused_for >= cfg.pause_timeout_seconds:
                        await self.shutdown(reason="pause_timeout", emit_event=True)
                        return
                continue
            is_silent = (now - self._last_activity_at) >= cfg.silence_end_seconds
            if is_silent and not self._generation_tasks and self._pending_decisions == 0:
                await self.shutdown(reason="silence_timeout", emit_event=True)
                return

    async def _generate_reply(self, agent_state: AgentState, decision: AgentDecision) -> str:
        info = agent_state.info
        current_task = asyncio.current_task()
        recent = self._get_recent_messages(limit=50)

        target_message = None
        if decision.target_message_id:
            for m in self.messages:
                if m.id == decision.target_message_id:
                    target_message = m
                    break

        target_msg_dict = (
            {
                "author_name": target_message.author_name,
                "content": target_message.content,
            }
            if target_message
            else None
        )

        recent_for_prompt = [
            {
                "author_name": m.author_name,
                "content": m.content,
                "target_author_name": m.target_author_name,
            }
            for m in recent
        ]
        def mark_output_started() -> None:
            if current_task is not None:
                self._generation_output_started[current_task] = True

        message_id, full_content = await generate_agent_reply(
            info=info,
            decision=decision,
            topic=self.topic,
            recent_messages=recent_for_prompt,
            target_message=target_msg_dict,
            emit=self.emit,
            mark_output_started=mark_output_started,
        )

        self.add_message(
            ChatMessage(
                id=message_id,
                author_type="ai",
                author_id=info.id,
                author_name=info.nickname,
                content=full_content,
                target_message_id=decision.target_message_id,
                target_author_name=decision.target_author_name,
            )
        )
        self._last_activity_at = time.time()
        self._total_ai_messages += 1
        agent_state.last_spoke_at = self._last_activity_at
        await self._notify_new_message()

        await self.emit(
            "message_completed",
            {
                "message_id": message_id,
                "author_type": "ai",
                "agent_id": info.id,
                "nickname": info.nickname,
                "content": full_content,
                "target_message_id": decision.target_message_id,
                "target_author_name": decision.target_author_name,
                "action": decision.action.value,
            },
        )

        return message_id
