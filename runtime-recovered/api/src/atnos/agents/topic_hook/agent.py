from __future__ import annotations

import html
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx

from atnos.agents.topic_hook.hook_generator import HookGenerator
from atnos.agents.topic_hook.models import RawTopic, ScoredTopic, Topic, TopicPackage, TopicRequest
from atnos.agents.topic_hook.scorer import TopicScorer
from atnos.core.config import Settings


RECOMMENDATION_THRESHOLD = 0.65


class TopicHookAgent:
    def __init__(
        self,
        settings: Settings,
        *,
        scorer: TopicScorer | None = None,
        hook_generator: HookGenerator | None = None,
    ) -> None:
        self.settings = settings
        self.scorer = scorer or TopicScorer()
        self.hook_generator = hook_generator or HookGenerator()

    def run(self, request: TopicRequest) -> TopicPackage:
        channel_dir = self._channel_dir(request.channel_id)
        channel_dir.mkdir(parents=True, exist_ok=True)
        target_count = max(5, request.target_count)

        self._log(request.channel_id, "[TOPIC] research start")
        raw_topics = self.research_topics(request, target_count=target_count * 2)
        self._log(request.channel_id, f"[TOPIC] research collected {len(raw_topics)} raw topics")

        scored_topics = self.scorer.score_topics(raw_topics, niche=request.niche, language=request.language)
        recommended = [topic for topic in scored_topics if topic.viral_score >= RECOMMENDATION_THRESHOLD]
        selected = _dedupe_scored_topics([*recommended, *scored_topics])[:target_count]
        self._log(request.channel_id, f"[TOPIC] scored {len(scored_topics)} topics")

        topics: list[Topic] = []
        for scored in selected:
            topics.append(
                Topic(
                    title=scored.title,
                    viral_score=scored.viral_score,
                    rationale=scored.rationale,
                    hooks=self.hook_generator.generate_hooks(
                        scored,
                        language=request.language,
                        style_guide=request.style_guide,
                    )[:3],
                    status="pending",
                    scores=scored.scores,
                )
            )
        self._log(request.channel_id, f"[TOPIC] hooks generated for {len(topics)} topics")

        package = TopicPackage(channel_id=request.channel_id, topics=topics)
        self._append_history(package)
        self._log(request.channel_id, f"[TOPIC] package saved {package.package_id}")
        return package

    def approve(self, package_id: str, topic_ids: list[str]) -> TopicPackage:
        package = self._load_package(package_id)
        wanted = set(topic_ids)
        if not wanted:
            raise ValueError("topic_ids cannot be empty.")

        updated_topics: list[Topic] = []
        approved_topics: list[Topic] = []
        for topic in package.topics:
            if topic.id in wanted:
                topic = topic.model_copy(update={"status": "approved"})
                approved_topics.append(topic)
            updated_topics.append(topic)
        if len(approved_topics) != len(wanted):
            found = {topic.id for topic in approved_topics}
            missing = sorted(wanted - found)
            raise ValueError(f"Unknown topic ids: {', '.join(missing)}")

        updated = package.model_copy(update={"topics": updated_topics})
        self._replace_history(updated)
        self._append_queue(updated.channel_id, approved_topics, updated.package_id)
        self._log(updated.channel_id, f"[TOPIC] approved {len(approved_topics)} topics from {package_id}")
        return updated

    def reject(self, package_id: str, topic_ids: list[str]) -> TopicPackage:
        package = self._load_package(package_id)
        wanted = set(topic_ids)
        if not wanted:
            raise ValueError("topic_ids cannot be empty.")

        updated_topics: list[Topic] = []
        rejected = 0
        for topic in package.topics:
            if topic.id in wanted:
                topic = topic.model_copy(update={"status": "rejected"})
                rejected += 1
            updated_topics.append(topic)
        if rejected != len(wanted):
            found = {topic.id for topic in package.topics if topic.id in wanted}
            missing = sorted(wanted - found)
            raise ValueError(f"Unknown topic ids: {', '.join(missing)}")

        updated = package.model_copy(update={"topics": updated_topics})
        self._replace_history(updated)
        self._log(updated.channel_id, f"[TOPIC] rejected {rejected} topics from {package_id}")
        return updated

    def latest_package(self, channel_id: str | None = None) -> TopicPackage | None:
        packages: list[TopicPackage] = []
        if channel_id:
            packages = self._load_history(channel_id)
        else:
            channels_root = self.settings.data_dir / "channels"
            if channels_root.exists():
                for channel_dir in channels_root.iterdir():
                    if channel_dir.is_dir():
                        packages.extend(self._load_history(channel_dir.name))
        if not packages:
            return None
        return sorted(packages, key=lambda item: item.generated_at, reverse=True)[0]

    def package_path(self, channel_id: str) -> Path:
        return self._history_path(channel_id)

    def queue_path(self, channel_id: str) -> Path:
        return self._queue_path(channel_id)

    def research_topics(self, request: TopicRequest, *, target_count: int) -> list[RawTopic]:
        candidates: list[RawTopic] = []
        candidates.extend(self._research_module_topics(request, target_count=target_count))
        candidates.extend(self._llm_topic_suggestions(request, target_count=target_count))
        candidates.extend(self._duckduckgo_topics(request.niche, request.language))
        candidates.extend(self._youtube_topics(request.niche, request.language))
        candidates.extend(self._seed_topics(request.niche, request.language))
        return _dedupe_topics(candidates)[: max(target_count, 12)]

    def _research_module_topics(self, request: TopicRequest, *, target_count: int) -> list[RawTopic]:
        if getattr(self.settings, "app_env", "") == "test" and os.getenv("ATNOS_ENABLE_RESEARCH_IN_TESTS") != "1":
            return []
        geo = "PL" if request.language.casefold().startswith("pl") else "US"
        try:
            from atnos.research.research_aggregator import find_best_topics_sync

            results = find_best_topics_sync(
                request.niche,
                geo=geo,
                language=request.language,
                topic_history=self._topic_history_titles(request.channel_id),
                count=min(max(target_count, 5), 10),
            )
        except Exception as exc:
            self._log(request.channel_id, f"[TOPIC] research module unavailable: {type(exc).__name__}")
            return []

        topics: list[RawTopic] = []
        for item in results:
            title = str(item.get("topic") or "").strip()
            if not title:
                continue
            evidence = []
            if item.get("snippet"):
                evidence.append(str(item["snippet"]))
            if item.get("trends_score") is not None:
                evidence.append(f"trends_score={float(item['trends_score']):.2f}")
            topics.append(
                RawTopic(
                    title=title,
                    source=f"research-{item.get('source') or 'unknown'}",
                    evidence=evidence,
                )
            )
        if topics:
            self._log(request.channel_id, f"[TOPIC] research module suggestions {len(topics)}")
        return topics[:target_count]

    def _llm_topic_suggestions(self, request: TopicRequest, *, target_count: int) -> list[RawTopic]:
        prompt = (
            "Return JSON only with key topics: array of short YouTube Shorts topic titles. "
            f"Niche: {request.niche}. Language: {request.language}. Count: {target_count}. "
            "Each topic must be concrete, explainable in 60 seconds, and not clickbait."
        )
        for provider_name, provider in (("ollama", self._ask_ollama), ("claude", self._ask_claude)):
            try:
                payload = provider(prompt)
            except Exception as exc:
                self._log(request.channel_id, f"[TOPIC] {provider_name} unavailable: {type(exc).__name__}")
                continue
            topics = _topics_from_payload(payload, source=provider_name)
            if topics:
                self._log(request.channel_id, f"[TOPIC] {provider_name} suggestions {len(topics)}")
                return topics[:target_count]
        return []

    def _ask_ollama(self, prompt: str) -> dict[str, Any]:
        model = self.settings.ollama_synthesis_model or self.settings.ollama_chat_model or "qwen3:14b"
        with httpx.Client(timeout=min(float(self.settings.ollama_timeout_s), 20.0)) as client:
            response = client.post(
                f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "format": "json",
                    "think": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
            message = payload.get("message") if isinstance(payload, dict) else None
            if isinstance(message, dict):
                text = str(message.get("content") or "")
            else:
                text = str(payload.get("response") or "") if isinstance(payload, dict) else ""
        return json.loads(text)

    def _ask_claude(self, prompt: str) -> dict[str, Any]:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is missing.")
        model = os.getenv("ANTHROPIC_SYNTHESIS_MODEL") or os.getenv("ANTHROPIC_CHAT_MODEL") or "claude-sonnet-4-20250514"
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 900,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
            payload = response.json()
        content = payload.get("content") or []
        text = ""
        if content and isinstance(content, list):
            text = str(content[0].get("text") or "")
        return json.loads(_extract_json(text))

    def _duckduckgo_topics(self, niche: str, language: str) -> list[RawTopic]:
        query = f"{niche} youtube shorts trending questions"
        url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            with httpx.Client(timeout=8.0, headers={"user-agent": "AtnosTopicHookAgent/1.0"}) as client:
                response = client.get(url)
                response.raise_for_status()
        except Exception:
            return []
        titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', response.text, flags=re.DOTALL | re.IGNORECASE)
        return [
            RawTopic(title=_clean_html(title), source="duckduckgo", evidence=[query])
            for title in titles[:8]
            if _clean_html(title)
        ]

    def _youtube_topics(self, niche: str, language: str) -> list[RawTopic]:
        query = f"{niche} shorts"
        url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
        try:
            with httpx.Client(timeout=8.0, headers={"user-agent": "AtnosTopicHookAgent/1.0"}) as client:
                response = client.get(url)
                response.raise_for_status()
        except Exception:
            return []
        titles = re.findall(r'"title":\{"runs":\[\{"text":"(.*?)"\}\]', response.text)
        return [
            RawTopic(title=_clean_json_text(title), source="youtube-public", evidence=[query])
            for title in titles[:10]
            if _clean_json_text(title)
        ]

    def _seed_topics(self, niche: str, language: str) -> list[RawTopic]:
        if language.casefold().startswith("pl"):
            templates = [
                "Dlaczego wszyscy myla sie co do {niche}",
                "3 bledy, ktore niszcza wyniki w {niche}",
                "Jak zrozumiec {niche} w 60 sekund",
                "Najprostsza zasada, ktora zmienia {niche}",
                "Bogaci robia to inaczej w temacie {niche}",
                "Tego nikt nie tlumaczy o {niche}",
                "Jedna rzecz, ktora blokuje postep w {niche}",
                "Czy {niche} nadal ma sens w 2026",
                "Najwiekszy mit o {niche}",
                "Prosta checklista dla {niche}",
            ]
        else:
            templates = [
                "Why everyone gets {niche} wrong",
                "3 mistakes that ruin results in {niche}",
                "Understand {niche} in 60 seconds",
                "The simplest rule that changes {niche}",
                "Rich people approach {niche} differently",
                "No one explains this about {niche}",
                "One thing blocking progress in {niche}",
                "Does {niche} still make sense in 2026",
                "The biggest myth about {niche}",
                "A simple checklist for {niche}",
            ]
        return [RawTopic(title=template.format(niche=niche), source="local-seed") for template in templates]

    def _channel_dir(self, channel_id: str) -> Path:
        safe = TopicRequest(channel_id=channel_id, niche="validation").channel_id
        return (self.settings.data_dir / "channels" / safe).resolve()

    def _history_path(self, channel_id: str) -> Path:
        return self._channel_dir(channel_id) / "topic_history.json"

    def _queue_path(self, channel_id: str) -> Path:
        return self._channel_dir(channel_id) / "topic_queue.json"

    def _log_path(self, channel_id: str) -> Path:
        return self._channel_dir(channel_id) / "topic_hook.log"

    def _log(self, channel_id: str, message: str) -> None:
        path = self._log_path(channel_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).isoformat()
        path.open("a", encoding="utf-8").write(f"{timestamp} {message}\n")

    def _load_history(self, channel_id: str) -> list[TopicPackage]:
        path = self._history_path(channel_id)
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError(f"Invalid topic history shape: {path}")
        return [TopicPackage.model_validate(item) for item in raw]

    def _topic_history_titles(self, channel_id: str) -> list[str]:
        try:
            packages = self._load_history(channel_id)
        except Exception:
            return []
        titles: list[str] = []
        for package in packages[-20:]:
            titles.extend(topic.title for topic in package.topics)
        return titles[-100:]

    def _append_history(self, package: TopicPackage) -> None:
        packages = self._load_history(package.channel_id)
        packages.append(package)
        self._write_history(package.channel_id, packages)

    def _replace_history(self, package: TopicPackage) -> None:
        packages = self._load_history(package.channel_id)
        replaced = False
        output: list[TopicPackage] = []
        for item in packages:
            if item.package_id == package.package_id:
                output.append(package)
                replaced = True
            else:
                output.append(item)
        if not replaced:
            raise ValueError(f"Unknown package_id: {package.package_id}")
        self._write_history(package.channel_id, output)

    def _write_history(self, channel_id: str, packages: list[TopicPackage]) -> None:
        path = self._history_path(channel_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [package.model_dump(mode="json") for package in packages]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_package(self, package_id: str) -> TopicPackage:
        latest = self.latest_package()
        packages: list[TopicPackage] = []
        if latest is not None:
            channels_root = self.settings.data_dir / "channels"
            for channel_dir in channels_root.iterdir():
                if channel_dir.is_dir():
                    packages.extend(self._load_history(channel_dir.name))
        for package in packages:
            if package.package_id == package_id:
                return package
        raise ValueError(f"Unknown package_id: {package_id}")

    def _append_queue(self, channel_id: str, topics: list[Topic], package_id: str) -> None:
        path = self._queue_path(channel_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError(f"Invalid topic queue shape: {path}")
        else:
            raw = []
        existing_ids = {str(item.get("id")) for item in raw if isinstance(item, dict)}
        for topic in topics:
            if topic.id in existing_ids:
                continue
            raw.append(
                {
                    "id": topic.id,
                    "package_id": package_id,
                    "channel_id": channel_id,
                    "title": topic.title,
                    "viral_score": topic.viral_score,
                    "rationale": topic.rationale,
                    "hooks": [hook.model_dump(mode="json") for hook in topic.hooks],
                    "status": "approved",
                    "approved_at": datetime.now(UTC).isoformat(),
                }
            )
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")


def _topics_from_payload(payload: dict[str, Any], *, source: str) -> list[RawTopic]:
    raw_topics = payload.get("topics")
    if not isinstance(raw_topics, list):
        return []
    topics: list[RawTopic] = []
    for item in raw_topics:
        title = item.get("title") if isinstance(item, dict) else item
        if isinstance(title, str) and title.strip():
            topics.append(RawTopic(title=title, source=source))
    return topics


def _extract_json(value: str) -> str:
    match = re.search(r"\{.*\}", value, flags=re.DOTALL)
    return match.group(0) if match else value


def _clean_html(value: str) -> str:
    value = re.sub(r"<.*?>", "", value)
    return " ".join(html.unescape(value).split())


def _clean_json_text(value: str) -> str:
    try:
        value = json.loads(f'"{value}"')
    except json.JSONDecodeError:
        pass
    return " ".join(str(value).split())


def _dedupe_topics(topics: list[RawTopic]) -> list[RawTopic]:
    seen: set[str] = set()
    output: list[RawTopic] = []
    for topic in topics:
        key = re.sub(r"[^a-z0-9]+", " ", topic.title.casefold()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(topic)
    return output


def _dedupe_scored_topics(topics: list[ScoredTopic]) -> list[ScoredTopic]:
    seen: set[str] = set()
    output = []
    for topic in topics:
        key = re.sub(r"[^a-z0-9]+", " ", topic.title.casefold()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(topic)
    return output
